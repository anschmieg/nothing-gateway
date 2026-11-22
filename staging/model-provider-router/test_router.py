import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import router
from admission_errors import classify

class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'key').write_text('pc-secret')
        self.cloud_calls=[]; self.local_calls=[]
        self.registered_local = True
        self.canonical_local = False
        self.native_error_status = None
        self.native_error_message = 'native route temporarily unavailable'
        async def cloud(request):
            if request.headers.get('Authorization') != 'Bearer gateway-key':
                return web.json_response({'error':'unauthorized'}, status=401)
            if request.path == '/v1/models':
                models = [{'id':'cloud/model'}]
                if self.registered_local:
                    local = {'id':'local/qwen3.8-heretic', 'owned_by':'local-pc'}
                    if self.canonical_local:
                        local={'id':'heretic','owned_by':'local-pc','overrides':['local-pc/heretic']}
                    elif request.headers.get('anthropic-version') or request.headers.get('User-Agent','').startswith('claude-cli'):
                        local['id']='claude-encoded-id'
                    models.append(local)
                return web.json_response({'object':'list','data':models}, headers={'Access-Control-Allow-Origin':'https://client.example'})
            if request.headers.get('Upgrade','').lower() == 'websocket':
                ws=web.WebSocketResponse();await ws.prepare(request)
                async for message in ws: await ws.send_str(message.data)
                return ws
            raw=await request.read();self.cloud_calls.append((request.path_qs,raw))
            if self.native_error_status:
                return web.json_response({'error':{'message':self.native_error_message}},status=self.native_error_status)
            return web.Response(body=raw or b'cloud',headers={'Content-Type':request.content_type})
        async def local(request):
            raw=await request.read();self.local_calls.append((request.path,raw,request.headers.get('Authorization')))
            if request.path.endswith('/content'):return web.Response(body=b'mp4-file', content_type='video/mp4')
            if request.path == '/v1/videos':return web.json_response({'id':'local-video','status':'queued'},status=202)
            if request.path == '/v1/images/generations':return web.json_response({'data':[{'url':'/v1/images/local-image/content'}]})
            if request.path == '/v1/chat/completions':
                if json.loads(raw).get('stream'):
                    response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
                    await response.prepare(request);await response.write(b'data: local\n\n');await response.write_eof();return response
                return web.json_response({'error':'unloaded'},status=503)
            return web.json_response({'id':'local-video','status':'completed'})
        ca=web.Application();ca.router.add_route('*','/{path:.*}',cloud)
        la=web.Application();la.router.add_route('*','/{path:.*}',local)
        self.cs=TestServer(ca);self.ls=TestServer(la)
        await self.cs.start_server();await self.ls.start_server()
        self.patch=patch.object(router,'cloud_url',return_value=str(self.cs.make_url('')).rstrip('/'));self.patch.start()
        self.controller_patch=patch.object(router,'ensure_controller',new_callable=AsyncMock)
        self.controller=self.controller_patch.start();self.controller.return_value=None
        self.client=TestClient(TestServer(router.make_app(self.root/'db',self.root/'key',str(self.ls.make_url('')))))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();await self.cs.close();await self.ls.close();self.patch.stop();self.controller_patch.stop();self.temp.cleanup()
    async def call(self,path,body=None,auth='gateway-key'):
        return await self.client.request('GET' if body is None else 'POST',path,json=body,headers={'Authorization':'Bearer '+auth})
    async def test_catalog_merged(self):
        response=await self.call('/v1/models');ids=[x['id'] for x in (await response.json())['data']]
        self.assertIn('cloud/model',ids);self.assertIn('local/qwen3.8-heretic',ids);self.assertIn('local/minimax-h3-high',ids)
    async def test_messages_preserves_translation_input(self):
        body={'model':'local/qwen3.8-heretic','max_tokens':256,'stream':True,
              'system':[{'type':'text','text':'Stable system', 'cache_control':{'type':'ephemeral'}}],
              'tools':[{'name':'lookup','input_schema':{'type':'object','properties':{}}}],
              'messages':[{'role':'assistant','content':[{'type':'tool_use','id':'call_1','name':'lookup','input':{}}]},
                          {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_1','content':'result'}]}]}
        r=await self.client.post('/v1/messages?beta=true',json=body,headers={'Authorization':'Bearer gateway-key','anthropic-version':'2023-06-01','User-Agent':'claude-cli/fixture'})
        self.assertEqual(r.status,200)
        self.assertEqual(await r.json(),body)
        self.assertEqual(self.cloud_calls[0][0],'/v1/messages?beta=true')
        self.assertFalse(self.local_calls)
        self.controller.assert_awaited_once()

    async def test_compressed_messages_pins_canonical_local_provider(self):
        import gzip
        self.canonical_local=True
        body={'model':'local/qwen3.8-heretic','max_tokens':32,'system':'Stable prefix','messages':[{'role':'user','content':'Hello'}]}
        r=await self.client.post('/v1/messages',data=gzip.compress(json.dumps(body).encode()),headers={'Authorization':'Bearer gateway-key','Content-Type':'application/json','Content-Encoding':'gzip','anthropic-version':'2023-06-01'})
        self.assertEqual(r.status,200)
        expected=dict(body,model='local-pc/heretic')
        self.assertEqual(await r.json(),expected)
        self.assertEqual(json.loads(self.cloud_calls[0][1]),expected)
        self.assertFalse(self.local_calls)

    async def test_translated_local_admission_metadata(self):
        defaults=dict(busy=False,running=False,action=None,resource_blocked=False,profile_conflict=False,failed=False,retry_seconds=60)
        for state in ({'busy':True},{'running':True,'action':'start'},{'running':True,'action':'stop'},{'resource_blocked':True},{'profile_conflict':True},{'failed':True}):
            status,kind,code,message,retry=classify(**dict(defaults,**state))
            self.native_error_status=status;self.native_error_message=message
            r=await self.call('/v1/messages',{'model':'local/qwen3.8-heretic'})
            self.assertEqual(r.status,status)
            self.assertEqual((await r.json())['error'],{'message':message,'code':code})
            self.assertEqual(r.headers.get('Retry-After'),str(retry) if retry and status!=502 else None)
            self.assertEqual(r.headers.get('Cache-Control'),'no-store')

    async def test_hidden_route_preserves_native_failure(self):
        self.registered_local=False
        self.native_error_status=502
        r=await self.call('/v1/messages',{'model':'local/qwen3.8-heretic'})
        self.assertEqual(r.status,502)
        self.assertEqual((await r.json())['error']['message'],'native route temporarily unavailable')
        self.assertEqual(json.loads(self.cloud_calls[0][1])['model'],'local-pc/heretic')
        self.assertFalse(self.local_calls)

    async def test_hidden_route_still_dispatches_exact_local_override(self):
        self.registered_local=False
        r=await self.call('/v1/messages',{'model':'local/qwen3.8-heretic'})
        self.assertEqual(r.status,200)
        self.assertEqual((await r.json())['model'],'local-pc/heretic')
        self.assertFalse(self.local_calls)

    async def test_messages_offline_does_not_dispatch(self):
        self.controller.return_value=router.retry_error('offline')
        r=await self.call('/v1/messages',{'model':'local/qwen3.8-heretic'})
        self.assertEqual(r.status,503);self.assertFalse(self.cloud_calls)

    async def test_messages_invalid_auth_does_not_wake_or_dispatch(self):
        r=await self.call('/v1/messages',{'model':'local/qwen3.8-heretic'},auth='bad')
        self.assertEqual(r.status,401);self.assertFalse(self.cloud_calls)
        self.controller.assert_not_awaited()
    async def test_native_auth_required_and_pc_key_not_gateway_key(self):
        for key in ('bad','pc-secret'):
            response=await self.call('/v1/videos',{'model':'local/minimax-h3-fast'},auth=key)
            self.assertEqual(response.status,401)
        self.assertFalse(self.local_calls)
    async def test_native_cors_preserved_on_catalog_local_errors_and_stream(self):
        for path, body in [('/v1/models', None), ('/v1/chat/completions', {'model':'local/qwen3.8-heretic'}), ('/v1/chat/completions', {'model':'local/qwen3.8-heretic','stream':True})]:
            response=await self.call(path,body)
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'),'https://client.example')
            await response.read()

    async def test_cloud_body_query_and_response_unchanged(self):
        response=await self.call('/v1/chat/completions?x=1',{'model':'cloud/model','stream':False})
        self.assertEqual((await response.json())['model'],'cloud/model')
        self.assertEqual(self.cloud_calls[0][0],'/v1/chat/completions?x=1');self.assertFalse(self.local_calls)
    async def test_gzip_cloud_body_and_compressed_local_alias(self):
        import gzip
        headers={'Authorization':'Bearer gateway-key','Content-Type':'application/json','Content-Encoding':'gzip'}
        raw=json.dumps({'model':'cloud/model'}).encode()
        response=await self.client.post('/v1/chat/completions',data=gzip.compress(raw),headers=headers)
        self.assertEqual((await response.json())['model'],'cloud/model')
        self.assertEqual(self.cloud_calls[-1][1],raw)
        before=len(self.cloud_calls)
        response=await self.client.post('/v1/chat/completions',data=gzip.compress(json.dumps({'model':'local/qwen3.8-heretic'}).encode()),headers=headers)
        self.assertEqual(response.status,503)
        self.assertEqual(len(self.cloud_calls),before)
        self.assertEqual(json.loads(self.local_calls[-1][1])['model'],'heretic')

    async def test_local_alias_and_error_never_fall_back(self):
        response=await self.call('/v1/chat/completions',{'model':'local/qwen3.8-heretic'})
        self.assertEqual(response.status,503)
        self.assertEqual(json.loads(self.local_calls[0][1])['model'],'heretic')
        self.assertEqual(self.local_calls[0][2],'Bearer pc-secret');self.assertFalse(self.cloud_calls)
    async def test_unknown_local_model_and_wrong_endpoint_never_cloud(self):
        self.assertEqual((await self.call('/v1/chat/completions',{'model':'local/unknown'})).status,404)
        self.assertEqual((await self.call('/v1/chat/completions',{'model':'local/minimax-h3-high'})).status,400)
        self.assertFalse(self.cloud_calls);self.assertFalse(self.local_calls)
    async def test_offline_pc_never_falls_back(self):
        # A closed loopback port simulates an offline PC after native authentication succeeds.
        self.client.server.app['local_url']='http://127.0.0.1:1'
        response=await self.call('/v1/chat/completions',{'model':'local/qwen3.8-heretic'})
        self.assertEqual(response.status,503)
        self.assertFalse(self.cloud_calls)

    async def test_multipart_cloud_body_preserved_and_local_refused(self):
        boundary='provider-test-boundary'
        def body(model):
            return ('--'+boundary+'\r\nContent-Disposition: form-data; name="model"\r\n\r\n'+model+'\r\n--'+boundary+'--\r\n').encode()
        headers={'Authorization':'Bearer gateway-key','Content-Type':'multipart/form-data; boundary='+boundary}
        raw=body('cloud/model')
        response=await self.client.post('/v1/images/edits',data=raw,headers=headers)
        self.assertEqual(await response.read(),raw)
        self.assertEqual(self.cloud_calls[-1][1],raw)
        before=len(self.cloud_calls)
        response=await self.client.post('/v1/images/edits',data=body('local/minimax-h3-image'),headers=headers)
        self.assertEqual(response.status,400)
        self.assertEqual(len(self.cloud_calls),before)

    async def test_local_stream(self):
        r=await self.call('/v1/chat/completions',{'model':'local/qwen3.8-heretic','stream':True})
        self.assertEqual(await r.read(),b'data: local\n\n')
    async def test_video_submission_poll_download_and_persisted_mapping(self):
        r=await self.call('/v1/videos',{'model':'local/minimax-h3-high','prompt':'x'})
        self.assertEqual(r.status,202);self.assertEqual(json.loads(self.local_calls[0][1])['model'],'minimax-h3-quality')
        self.assertEqual(self.client.server.app['db'].execute('select kind from media where id=?',('local-video',)).fetchone()[0],'video')
        r=await self.call('/v1/videos/local-video');self.assertEqual((await r.json())['status'],'completed')
        r=await self.call('/v1/videos/local-video/content');self.assertEqual(await r.read(),b'mp4-file');self.assertFalse(self.cloud_calls)
    async def test_image_url_routes_download(self):
        r=await self.call('/v1/images/generations',{'model':'local/minimax-h3-image','prompt':'x'})
        url=(await r.json())['data'][0]['url'];r=await self.call(url);self.assertEqual(await r.read(),b'mp4-file')
    async def test_unregistered_cloud_media_stays_cloud(self):
        r=await self.call('/v1/videos/cloud-video/content');self.assertEqual(await r.read(),b'cloud');self.assertFalse(self.local_calls)
    async def test_cloud_websocket_passthrough(self):
        ws=await self.client.ws_connect('/v1/responses',headers={'Authorization':'Bearer gateway-key'})
        await ws.send_json({'model':'cloud/model'});self.assertEqual((await ws.receive_json())['model'],'cloud/model');await ws.close()
    async def test_local_websocket_refused_without_cloud_inference(self):
        ws=await self.client.ws_connect('/v1/responses',headers={'Authorization':'Bearer gateway-key'})
        await ws.send_json({'response':{'model':'local/qwen3.8-heretic'}})
        self.assertEqual((await ws.receive_json())['type'],'error');await ws.close()

if __name__=='__main__': unittest.main()
