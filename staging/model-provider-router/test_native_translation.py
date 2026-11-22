"""Exercise the deployed native binary against a loopback-only chat fixture.

Run with NATIVE_GATEWAY_BINARY pointing to an explicit binary. Synthetic keys,
empty auth directory and disabled plugins; never uses live provider config.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from aiohttp import ClientSession, web
from local_registration import local_allowance
from aiohttp.test_utils import TestServer


@unittest.skipUnless(os.environ.get('NATIVE_GATEWAY_BINARY'), 'explicit native binary required')
class NativeTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_system_stream_and_stable_prefix(self):
        await self.check_translation(False)

    async def test_canonical_provider_override(self):
        await self.check_translation(True)

    async def test_cold_start_503_does_not_disable_next_request(self):
        await self.check_translation(True, startup_error=True)

    async def test_translated_admission_errors(self):
        await self.check_translation(True, error_states=True)

    async def check_translation(self, canonical, startup_error=False, error_states=False):
        calls = []
        error_cases=[(429,'Local model is busy processing another request.','15'),
                     (503,'Local model is loading. Startup continues in the background.','15'),
                     (409,'Local inference is blocked by other GPU work.',None),
                     (502,'Local model startup failed. Inspect local controller status.','60')]
        async def upstream(request):
            self.assertEqual(request.path, '/v1/chat/completions')
            self.assertEqual(request.headers.get('Authorization'), 'Bearer fixture-pc-key')
            body = await request.json(); calls.append(body)
            if error_states:
                status,message,retry=error_cases[len(calls)-1]
                return web.json_response({'error':{'type':'api_error','code':'fixture_admission','message':message}},status=status,headers={'Retry-After':retry} if retry else {})
            if startup_error and len(calls)==1:
                return web.json_response({'error':{'type':'service_unavailable','code':'model_not_ready','message':'Model loading; retry shortly.'}},status=503,headers={'Retry-After':'15'})
            choice = {'index':0, 'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_2','type':'function','function':{'name':'lookup','arguments':'{"key":"next"}'}}]},'finish_reason':'tool_calls'}
            value = {'id':'chatcmpl-fixture','object':'chat.completion','model':'heretic','choices':[choice],'usage':{'prompt_tokens':24,'completion_tokens':8,'total_tokens':32,'prompt_tokens_details':{'cached_tokens':16}}}
            if not body.get('stream'):
                return web.json_response(value)
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request)
            for delta, finish in [({'role':'assistant','tool_calls':[{'index':0,'id':'call_2','type':'function','function':{'name':'lookup','arguments':''}}]},None), ({'tool_calls':[{'index':0,'function':{'arguments':'{"key":"next"}'}}]},None), ({},'tool_calls')]:
                chunk={'id':'chatcmpl-fixture','object':'chat.completion.chunk','model':'heretic','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                await response.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            await response.write(b'data: [DONE]\n\n');await response.write_eof();return response
        app=web.Application();app.router.add_post('/v1/chat/completions',upstream)
        async with TestServer(app) as fixture:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                config={'config-version':8,'server':{'host':'127.0.0.1','port':port},'access':{'api-keys':['fixture-client-key']},'management':{'disable-control-panel':True,'disable-auto-update-panel':True},'oauth':{'auth-dir':str(root/'auth')},'plugins':{'enabled':False},'models':{'canonical-only':canonical,'catalog':'','codex-catalog':'','devin-catalog':''},'api-keys':{'openai-compatibility':[{'name':'local-pc','base-url':str(fixture.make_url('/v1')),'request-retry':0,'disable-redirects':True,'keys':[{'api-key':'fixture-pc-key'}],'models':[{'name':'heretic','alias':'local/qwen3.8-heretic'}]}]}}
                if startup_error or error_states:
                    config['api-keys']['openai-compatibility'][0]['disable-cooling']=True
                if canonical:
                    config['api-keys']['openai-compatibility'][0]['models'][0].pop('alias')
                    config['models']['canonical-routing']={'no-charge-allowances':[local_allowance('fixture-pc-key',str(fixture.make_url('/v1')))]}
                (root/'config.json').write_text(json.dumps(config))
                with (root/'gateway.log').open('wb') as log:
                    proc=await asyncio.create_subprocess_exec(os.environ['NATIVE_GATEWAY_BINARY'],'--config',str(root/'config.json'),'--local-model',cwd=root,env={'PATH':os.environ['PATH'],'HOME':tmp},stdout=log,stderr=log)
                    try:
                        async with ClientSession(headers={'Authorization':'Bearer fixture-client-key','anthropic-version':'2023-06-01','x-session-id':'fixture-stable-session'}) as client:
                            base=f'http://127.0.0.1:{port}'
                            for _ in range(100):
                                try:
                                    async with client.get(base+'/v1/models',headers={'anthropic-version':''}) as r:
                                        if r.status==200:
                                            catalog=await r.json()
                                            if any(m.get('owned_by')=='local-pc' for m in catalog.get('data',[])):break
                                except OSError:pass
                                await asyncio.sleep(.05)
                            else:self.fail('isolated native catalog: '+json.dumps(catalog))
                            models=[m for m in catalog['data'] if m.get('owned_by')=='local-pc']
                            self.assertEqual(len(models),1);self.assertEqual(models[0]['owned_by'],'local-pc')
                            if error_states:
                                for status,message,retry in error_cases:
                                    async with client.post(base+'/v1/messages',json={'model':'local-pc/heretic','max_tokens':32,'messages':[{'role':'user','content':'fixture'}]}) as r:
                                        result=await r.json()
                                        self.assertEqual(r.status,status,result)
                                        self.assertEqual(result['error']['message'],message)
                                        # The local edge restores this header from the shared admission catalog.
                                        self.assertIn(r.headers.get('Retry-After'),(None,retry))
                                return
                            body={'model':'local-pc/heretic' if canonical else 'local/qwen3.8-heretic','max_tokens':256,'system':[{'type':'text','text':'Stable prefix','cache_control':{'type':'ephemeral'}}],'tools':[{'name':'lookup','description':'Lookup key','input_schema':{'type':'object','properties':{'key':{'type':'string'}},'required':['key']}}],'messages':[{'role':'user','content':'First'},{'role':'assistant','content':[{'type':'tool_use','id':'call_1','name':'lookup','input':{'key':'first'}}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'call_1','content':'result'}]}]}
                            if startup_error:
                                async with client.post(base+'/v1/messages',json=body) as first:
                                    self.assertEqual(first.status,503,await first.text())
                            async with client.post(base+'/v1/messages',json=body) as r:
                                result=await r.json();self.assertEqual(r.status,200,result)
                            self.assertEqual(result['usage'].get('cache_read_input_tokens'),16);self.assertEqual(result['type'],'message');self.assertEqual(result['stop_reason'],'tool_use')
                            tool=next(x for x in result['content'] if x['type']=='tool_use')
                            self.assertEqual(tool['id'],'call_2');self.assertEqual(tool['input'],{'key':'next'})
                            if canonical:
                                config['access']['api-keys'].append('fixture-second-key')
                                config['api-keys']['openai-compatibility'][0]['models'][0]['max-context-length']=94208
                                (root/'config.json').write_text(json.dumps(config))
                                await asyncio.sleep(2)
                            body['stream']=True
                            async with client.post(base+'/v1/messages',json=body) as r:
                                raw=await r.text();self.assertEqual(r.status,200,raw)
                            self.assertIn('event: message_start',raw);self.assertIn('event: message_stop',raw)
                            self.assertIn('input_json_delta',raw);self.assertIn('call_2',raw)
                            self.assertEqual(len(calls),3 if startup_error else 2)
                            for sent in calls:
                                self.assertEqual(sent['model'],'heretic')
                                self.assertIn('Stable prefix',json.dumps(sent['messages'][0]))
                                self.assertEqual(sent['tools'][0]['function']['name'],'lookup')
                                self.assertEqual(sent['messages'][-1]['role'],'tool')
                                self.assertEqual(sent['messages'][-1]['tool_call_id'],'call_1')
                            self.assertEqual(calls[0]['messages'],calls[1]['messages'])
                            self.assertEqual(calls[0]['tools'],calls[1]['tools'])
                    finally:

                        if proc.returncode is None:
                            proc.terminate();await asyncio.wait_for(proc.wait(),10)

if __name__=='__main__':unittest.main()
