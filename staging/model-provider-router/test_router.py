import asyncio
from email.utils import formatdate
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp import ClientPayloadError, ClientSession, web
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
        self.native_handler = None
        self.local_handler = None
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
            if self.native_handler is not None:
                return await self.native_handler(request)
            if self.native_error_status:
                return web.json_response({'error':{'message':self.native_error_message}},status=self.native_error_status)
            return web.Response(body=raw or b'cloud',headers={'Content-Type':request.content_type})
        async def local(request):
            raw=await request.read();self.local_calls.append((request.path,raw,request.headers.get('Authorization')))
            if self.local_handler is not None:
                return await self.local_handler(request)
            if request.path.endswith('/content'):return web.Response(body=b'mp4-file', content_type='video/mp4')
            if request.path == '/v1/videos':return web.json_response({'id':'local-video','status':'queued'},status=202)
            if request.path == '/v1/images/generations':return web.json_response({'data':[{'url':'/v1/images/local-image/content'}]})
            if request.path == '/v1/chat/completions':
                if json.loads(raw).get('stream'):
                    response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
                    await response.prepare(request);await response.write(b'data: local\n\ndata: [DONE]\n\n');await response.write_eof();return response
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

    async def test_stream_pings_before_headers_and_first_model_bytes(self):
        streams = (
            ('/v1/messages', b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
             b'event: ping\ndata: {"type":"ping"}\n\n'),
            ('/v1/responses', b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
             b': keep-alive\n\n'),
            ('/v1/chat/completions', b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
             b': keep-alive\n\n'),
        )
        with patch.dict(router.ALIASES, {'local/another-model': ('another-backend', 'llm')}):
            for path, frame, ping in streams:
                for model in ('local/qwen3.8-heretic', 'local/another-model', 'cloud/uncataloged-model', None):
                    with self.subTest(path=path, model=model):
                        await self.check_delayed_stream(path, frame, ping, model)

    async def check_delayed_stream(self, path, frame, ping, model):
        send_headers = asyncio.Event()
        headers_sent = asyncio.Event()
        send_body = asyncio.Event()

        async def upstream(request):
            await send_headers.wait()
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            headers_sent.set()
            await send_body.wait()
            await response.write(frame)
            return response

        self.native_handler = upstream
        self.local_handler = upstream
        self.cloud_calls.clear()
        self.local_calls.clear()
        body = {'stream': True}
        if model is not None:
            body['model'] = model
        local = model in router.ALIASES
        with patch.multiple(router, FIRST_STREAM_PING=0.02, STREAM_PING_INTERVAL=0.02):
            try:
                response = await asyncio.wait_for(self.call(path, body), 2)
                self.assertEqual(response.status, 200)
                if local:
                    self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://client.example')
                self.assertEqual(await asyncio.wait_for(response.content.readexactly(len(ping)), 2), ping)
                self.assertFalse(headers_sent.is_set())
                send_headers.set()
                await asyncio.wait_for(headers_sent.wait(), 2)
                self.assertEqual(await asyncio.wait_for(response.content.readexactly(len(ping)), 2), ping)
                send_body.set()
                rest = await asyncio.wait_for(response.read(), 2)
                self.assertTrue(rest.endswith(frame))
                self.assertEqual(rest[:-len(frame)].replace(ping, b''), b'')
                if path == '/v1/messages' or not local:
                    self.assertEqual(len(self.cloud_calls), 1)
                    upstream_path, upstream_body = self.cloud_calls[0]
                    self.assertEqual(upstream_path, path)
                    expected = dict(body, model='local-pc/another-backend') if model == 'local/another-model' else body
                    self.assertEqual(json.loads(upstream_body), expected)
                    self.assertFalse(self.local_calls)
                else:
                    self.assertFalse(self.cloud_calls)
                    self.assertEqual(len(self.local_calls), 1)
                    upstream_path, upstream_body, auth = self.local_calls[0]
                    self.assertEqual(upstream_path, path)
                    self.assertEqual(json.loads(upstream_body), dict(body, model=router.ALIASES[model][0]))
                    self.assertEqual(auth, 'Bearer pc-secret')
            finally:
                send_headers.set()
                send_body.set()

    async def test_cloud_messages_late_error_uses_messages_format(self):
        finish = asyncio.Event()
        detail = {'type': 'rate_limit_error', 'message': 'Provider is busy.'}

        async def upstream(request):
            await finish.wait()
            return web.json_response({'error': detail}, status=429, headers={'Retry-After': '15'})

        self.native_handler = upstream
        body = {'model': 'cloud/model', 'stream': True}
        with patch.object(router, 'FIRST_STREAM_PING', 0.02):
            try:
                response = await asyncio.wait_for(self.call('/v1/messages', body), 2)
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.content.readexactly(len(router.MESSAGES_PING)), router.MESSAGES_PING)
                finish.set()
                event, data, _ = (await asyncio.wait_for(response.text(), 2)).split('\n', 2)
                self.assertEqual(event, 'event: error')
                self.assertEqual(json.loads(data.removeprefix('data: ')),
                                 {'type': 'error', 'error': dict(detail, retry_after=15)})
            finally:
                finish.set()

    async def test_cloud_browser_waits_for_cors_and_request_id_then_pings(self):
        started = asyncio.Event()
        send_headers = asyncio.Event()
        send_body = asyncio.Event()

        async def upstream(request):
            started.set()
            await send_headers.wait()
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream',
                'Access-Control-Allow-Origin': 'https://client.example', 'X-Request-Id': 'fixture-id'})
            await response.prepare(request)
            await send_body.wait()
            await response.write(b'data: [DONE]\n\n')
            return response

        self.native_handler = upstream
        with patch.multiple(router, FIRST_STREAM_PING=0, STREAM_PING_INTERVAL=0.02):
            pending = asyncio.create_task(self.client.post('/v1/chat/completions',
                json={'model': 'cloud/model', 'stream': True}, headers={
                    'Authorization': 'Bearer gateway-key', 'Origin': 'https://client.example'}))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertFalse((await asyncio.wait((pending,), timeout=0.05))[0])
                send_headers.set()
                response = await asyncio.wait_for(pending, 2)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://client.example')
                self.assertEqual(response.headers['X-Request-Id'], 'fixture-id')
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
            finally:
                send_headers.set()
                send_body.set()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_stream_keeps_pinging_between_frames_without_splitting_crlf(self):
        send_partial = asyncio.Event()
        complete_cr_frame = asyncio.Event()
        finish_frame = asyncio.Event()
        send_terminal = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(b': upstream heartbeat\r\n\r\n')
            await send_partial.wait()
            await response.write(b'data: partial')
            await complete_cr_frame.wait()
            await response.write(b'\r\n\r')
            await finish_frame.wait()
            await response.write(b'\n')
            await send_terminal.wait()
            await response.write(b'data: [DONE]\n\n')
            return response

        self.native_handler = upstream
        with patch.object(router, 'STREAM_PING_INTERVAL', 0.02):
            try:
                response = await self.call('/v1/chat/completions', {'model': 'cloud/model', 'stream': True})
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\r\n\r\n'), 2),
                                 b': upstream heartbeat\r\n\r\n')
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                send_partial.set()
                self.assertTrue((await asyncio.wait_for(response.content.readuntil(b'data: partial'), 2)).endswith(b'data: partial'))
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(response.content.readany(), 0.06)
                complete_cr_frame.set()
                self.assertEqual(await asyncio.wait_for(response.content.readexactly(4), 2), b'\r\n\r\n')
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                finish_frame.set()
                send_terminal.set()
                rest = await asyncio.wait_for(response.read(), 2)
                self.assertEqual(rest.replace(router.OPENAI_PING, b''), b'data: [DONE]\n\n')
            finally:
                send_partial.set()
                complete_cr_frame.set()
                finish_frame.set()
                send_terminal.set()

    async def test_cr_only_event_allows_keepalive(self):
        finish = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(b'data: [DONE]\r\r')
            await finish.wait()
            return response

        self.native_handler = upstream
        with patch.object(router, 'STREAM_PING_INTERVAL', 0.02):
            try:
                response = await self.call('/v1/chat/completions', {'model': 'cloud/model', 'stream': True})
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\r\r'), 2), b'data: [DONE]\r\r')
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), b'\n' + router.OPENAI_PING)
                finish.set()
                self.assertEqual((await asyncio.wait_for(response.read(), 2)).replace(router.OPENAI_PING, b''), b'')
            finally:
                finish.set()

    async def test_split_upstream_bom_is_removed_after_keepalive(self):
        send_headers = asyncio.Event()
        finish_bom = asyncio.Event()

        async def upstream(request):
            await send_headers.wait()
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(b'\xef')
            await finish_bom.wait()
            await response.write(b'\xbb\xbfdata: [DONE]\n\n')
            return response

        self.native_handler = upstream
        with patch.multiple(router, FIRST_STREAM_PING=0.02, STREAM_PING_INTERVAL=0.02):
            try:
                response = await asyncio.wait_for(self.call('/v1/chat/completions', {
                    'model': 'cloud/model', 'stream': True}), 2)
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                send_headers.set()
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                finish_bom.set()
                rest = await asyncio.wait_for(response.read(), 2)
                self.assertEqual(rest.replace(router.OPENAI_PING, b''), b'data: [DONE]\n\n')
            finally:
                send_headers.set()
                finish_bom.set()

    async def test_disconnect_cancels_stream_with_production_server_defaults(self):
        ready = asyncio.Event()
        disconnected = asyncio.Event()

        async def upstream(request):
            ready.set()
            try:
                await asyncio.Future()
            finally:
                disconnected.set()

        self.native_handler = upstream
        app = router.make_app(self.root/'production-db', self.root/'key', str(self.ls.make_url('')))
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        host, port = runner.addresses[0]
        async with ClientSession() as session:
            with patch.multiple(router, FIRST_STREAM_PING=10, STREAM_PEER_CHECK_INTERVAL=0.02):
                pending = asyncio.create_task(session.post(f'http://{host}:{port}/v1/responses',
                    json={'model': 'cloud/model', 'stream': True}, headers={'Authorization': 'Bearer gateway-key'}))
                try:
                    await asyncio.wait_for(ready.wait(), 2)
                finally:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await asyncio.wait_for(disconnected.wait(), 2)

    async def test_stalled_local_error_body_preserves_http_status_and_retry_after(self):
        finish = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(status=429, headers={
                'Content-Type': 'application/json', 'Retry-After': '15'})
            await response.prepare(request)
            await finish.wait()
            return response

        self.local_handler = upstream
        self.native_handler = upstream
        with patch.object(router, 'STREAM_ERROR_BODY_TIMEOUT', 0.02):
            try:
                for path in ('/v1/responses', '/v1/messages'):
                    with self.subTest(path=path):
                        response = await asyncio.wait_for(self.call(path, {
                            'model': 'local/qwen3.8-heretic', 'stream': True}), 2)
                        self.assertEqual(response.status, 429)
                        self.assertEqual(response.headers['Retry-After'], '15')
                        self.assertEqual((await response.json())['error']['message'], 'Upstream error body was unavailable.')
            finally:
                finish.set()

    async def test_malformed_local_error_preserves_status_and_retry_after(self):
        async def upstream(request):
            return web.Response(status=429, body=b'{malformed', headers={
                'Content-Type': 'application/json', 'Retry-After': '15'})

        self.local_handler = upstream
        self.native_handler = upstream
        for path in ('/v1/responses', '/v1/messages'):
            with self.subTest(path=path):
                response = await self.call(path, {'model': 'local/qwen3.8-heretic', 'stream': True})
                self.assertEqual(response.status, 429)
                self.assertEqual(response.headers['Retry-After'], '15')
                self.assertEqual((await response.json())['error']['message'], 'Upstream error body was unavailable.')

    async def test_late_error_converts_http_date_retry_after(self):
        finish = asyncio.Event()
        retry_at = formatdate(time.time() + 30, usegmt=True)

        async def upstream(request):
            await finish.wait()
            return web.json_response({'error': {'message': 'Rate limited.'}}, status=429,
                                     headers={'Retry-After': retry_at})

        self.native_handler = upstream
        with patch.object(router, 'FIRST_STREAM_PING', 0.02):
            try:
                response = await asyncio.wait_for(self.call('/v1/responses', {
                    'model': 'cloud/model', 'stream': True}), 2)
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                finish.set()
                event, data, _ = (await asyncio.wait_for(response.text(), 2)).split('\n', 2)
                self.assertEqual(event, 'event: error')
                error = json.loads(data.removeprefix('data: '))
                self.assertEqual(error['message'], 'Rate limited.')
                self.assertIn(error['retry_after'], range(28, 31))
            finally:
                finish.set()

    async def test_empty_and_comment_only_sse_after_keepalive_emit_error(self):
        for body in (b'', b': upstream heartbeat\n\n', b': upstream heartbeat\r\r'):
            with self.subTest(body=body):
                await self.check_sse_without_data(body)

    async def check_sse_without_data(self, body):
        finish = asyncio.Event()

        async def upstream(request):
            await finish.wait()
            return web.Response(body=body, content_type='text/event-stream')

        self.local_handler = upstream
        with patch.object(router, 'FIRST_STREAM_PING', 0.02):
            try:
                response = await asyncio.wait_for(self.call('/v1/responses', {
                    'model': 'local/qwen3.8-heretic', 'stream': True}), 2)
                self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2), router.OPENAI_PING)
                finish.set()
                raw = await asyncio.wait_for(response.read(), 2)
                self.assertTrue(raw.startswith(body))
                event, data, _ = raw[len(body):].lstrip(b'\n').decode().split('\n', 2)
                self.assertEqual(event, 'event: error')
                self.assertEqual(json.loads(data.removeprefix('data: '))['message'], 'Upstream closed without sending events.')
            finally:
                finish.set()

    async def test_eof_without_terminal_event_emits_protocol_error(self):
        cases = (
            ('/v1/messages', b'event: ping\ndata: {"type":"ping"}\n\n'),
            ('/v1/responses', b'data: {"type":"response.created","sequence_number":8}\n\n'),
            ('/v1/chat/completions', b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),
        )
        for path, frame in cases:
            with self.subTest(path=path):
                async def upstream(request):
                    return web.Response(body=frame, content_type='text/event-stream')

                self.native_handler = upstream
                response = await self.call(path, {'model': 'cloud/model', 'stream': True})
                raw = await response.read()
                self.assertTrue(raw.startswith(frame))
                error = json.loads(raw[len(frame):].removeprefix(b'event: error\n').removeprefix(b'data: '))
                if path == '/v1/responses':
                    self.assertEqual(error['sequence_number'], 9)
                else:
                    error = error['error']
                self.assertEqual(error['code'], 'incomplete_stream')

    async def test_terminal_events_and_upstream_errors_pass_through(self):
        cases = (
            ('/v1/messages', b'event: error\ndata: {"type":"error","error":{"message":"busy"}}\n\n'),
            ('/v1/responses', b'data: {"type":"response.failed"}\n\n'),
            ('/v1/responses', b'data: {\r\ndata: "type":"response.incomplete"}\r\n\r\n'),
            ('/v1/chat/completions', b'data: {"error":{"message":"busy"}}\n\n'),
        )
        for path, frame in cases:
            with self.subTest(path=path, frame=frame):
                async def upstream(request):
                    return web.Response(body=frame, content_type='text/event-stream')

                self.native_handler = upstream
                response = await self.call(path, {'model': 'cloud/model', 'stream': True})
                self.assertEqual(await response.read(), frame)

    async def test_large_named_terminal_event_passes_through(self):
        frame = b'event: response.completed\ndata: {"output":"' + b'x' * 100 + b'"}\n\n'

        async def upstream(request):
            return web.Response(body=frame, content_type='text/event-stream')

        self.native_handler = upstream
        with patch.object(router, 'SSE_INSPECTION_LIMIT', 64):
            response = await self.call('/v1/responses', {'model': 'cloud/model', 'stream': True})
            self.assertEqual(await response.read(), frame)

    async def test_stalled_partial_frame_aborts_without_inserting_bytes(self):
        release = asyncio.Event()
        partial = b'data: {"type":"response.created"}\n'

        async def upstream(request):
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(partial)
            await release.wait()
            return response

        self.native_handler = upstream
        with patch.multiple(router, STREAM_PARTIAL_FRAME_TIMEOUT=0.1, STREAM_PING_INTERVAL=0.02):
            try:
                response = await self.call('/v1/responses', {'model': 'cloud/model', 'stream': True})
                self.assertEqual(await asyncio.wait_for(response.content.readexactly(len(partial)), 2), partial)
                with self.assertRaises(ClientPayloadError):
                    await asyncio.wait_for(response.content.readany(), 2)
            finally:
                release.set()

    async def test_eof_inside_frame_aborts_even_after_model_data(self):
        frame = (b'data: {"type":"response.created"}\n\n'
                 b'data: {"type":"response.completed"}\n')

        async def upstream(request):
            return web.Response(body=frame, content_type='text/event-stream')

        self.native_handler = upstream
        response = await self.call('/v1/responses', {'model': 'cloud/model', 'stream': True})
        self.assertEqual(await response.content.readexactly(len(frame)), frame)
        with self.assertRaises(ClientPayloadError):
            await response.content.readany()

    async def test_late_local_nonstream_success_does_not_wait_for_body(self):
        send_headers = asyncio.Event()
        send_body = asyncio.Event()

        async def upstream(request):
            await send_headers.wait()
            response = web.StreamResponse(headers={'Content-Type': 'application/json'})
            await response.prepare(request)
            await send_body.wait()
            return response

        self.local_handler = upstream
        with patch.object(router, 'FIRST_STREAM_PING', 0.02):
            try:
                response = await self.call('/v1/responses', {'model': 'local/qwen3.8-heretic', 'stream': True})
                self.assertEqual(await response.content.readuntil(b'\n\n'), router.OPENAI_PING)
                send_headers.set()
                raw = await asyncio.wait_for(response.read(), 2)
                self.assertIn(b'Upstream did not return an event stream.', raw)
            finally:
                send_headers.set()
                send_body.set()

    async def test_late_error_preserves_retry_after_across_content_types(self):
        cases = (
            ('text/html', '<h1>Busy</h1>', 'Upstream did not return an event stream.'),
            ('application/problem+json', '{"title":"Busy","detail":"Upstream is busy."}', 'Upstream is busy.'),
        )
        for content_type, body, message in cases:
            with self.subTest(content_type=content_type):
                send_headers = asyncio.Event()

                async def upstream(request):
                    await send_headers.wait()
                    return web.Response(status=503, text=body, content_type=content_type,
                                        headers={'Retry-After': '15'})

                self.native_handler = upstream
                with patch.object(router, 'FIRST_STREAM_PING', 0.02):
                    try:
                        response = await self.call('/v1/responses', {'model': 'cloud/model', 'stream': True})
                        self.assertEqual(await response.content.readuntil(b'\n\n'), router.OPENAI_PING)
                        send_headers.set()
                        raw = await asyncio.wait_for(response.read(), 2)
                        error = json.loads(raw.removeprefix(b'event: error\ndata: '))
                        self.assertEqual(error['retry_after'], 15)
                        self.assertEqual(error['message'], message)
                    finally:
                        send_headers.set()

    async def test_cloud_buffered_request_does_not_commit_before_upstream(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def upstream(request):
            started.set()
            await finish.wait()
            return web.json_response({'error': {'message': 'Provider is busy.'}}, status=429)

        self.native_handler = upstream
        with patch.object(router, 'FIRST_STREAM_PING', 0):
            pending = asyncio.create_task(self.call('/v1/responses', {'model': 'cloud/model', 'stream': False}))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertFalse((await asyncio.wait((pending,), timeout=0.05))[0])
                finish.set()
                response = await asyncio.wait_for(pending, 2)
                self.assertEqual(response.status, 429)
                self.assertEqual(await response.json(), {'error': {'message': 'Provider is busy.'}})
            finally:
                finish.set()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_messages_stream_preserves_early_admission_error(self):
        status, kind, code, message, retry = classify(busy=True, running=False, action=None,
            resource_blocked=False, profile_conflict=False, failed=False, retry_seconds=0)
        self.native_error_status = status
        self.native_error_message = message
        response = await self.call('/v1/messages', {'model': 'local/qwen3.8-heretic', 'stream': True})
        self.assertEqual(response.status, status)
        self.assertEqual(response.headers['Retry-After'], str(retry))
        self.assertEqual((await response.json())['error']['code'], code)

    async def test_messages_stream_reports_late_admission_error_as_sse(self):
        finish = asyncio.Event()
        status, kind, code, message, retry = classify(busy=True, running=False, action=None,
            resource_blocked=False, profile_conflict=False, failed=False, retry_seconds=0)

        async def upstream(request):
            await finish.wait()
            return web.json_response({'error': {'type': kind, 'message': message}}, status=status)

        self.native_handler = upstream
        with patch.object(router, 'FIRST_STREAM_PING', 0.02):
            try:
                response = await asyncio.wait_for(self.call('/v1/messages', {
                    'model': 'local/qwen3.8-heretic', 'stream': True}), 2)
                self.assertEqual(response.status, 200)
                self.assertEqual(await asyncio.wait_for(response.content.readexactly(len(router.MESSAGES_PING)), 2),
                                 router.MESSAGES_PING)
                finish.set()
                event, data, _ = (await asyncio.wait_for(response.text(), 2)).split('\n', 2)
                self.assertEqual(event, 'event: error')
                self.assertEqual(json.loads(data.removeprefix('data: ')), {'type': 'error', 'error': {
                    'type': kind, 'message': message, 'code': code, 'retry_after': retry}})
                self.assertEqual(len(self.cloud_calls), 1)
            finally:
                finish.set()

    async def test_openai_stream_preserves_early_errors_and_encodes_late_errors(self):
        detail = {'type': 'rate_limit_error', 'code': 'concurrency_limit_exceeded', 'message': 'Local model is busy.'}
        body = {'model': 'local/qwen3.8-heretic', 'stream': True}
        for path in ('/v1/responses', '/v1/chat/completions'):
            with self.subTest(path=path):
                finish = asyncio.Event()
                finish.set()

                async def upstream(request):
                    await finish.wait()
                    return web.json_response({'error': detail}, status=429, headers={'Retry-After': '15'})

                self.local_handler = upstream
                response = await self.call(path, body)
                self.assertEqual(response.status, 429)
                self.assertEqual(response.headers['Retry-After'], '15')
                self.assertEqual(await response.json(), {'error': detail})

                finish.clear()
                with patch.object(router, 'FIRST_STREAM_PING', 0.02):
                    try:
                        response = await asyncio.wait_for(self.call(path, body), 2)
                        self.assertEqual(response.status, 200)
                        comment = b': keep-alive\n\n'
                        self.assertEqual(await asyncio.wait_for(response.content.readexactly(len(comment)), 2), comment)
                        finish.set()
                        raw = await asyncio.wait_for(response.text(), 2)
                        if path == '/v1/responses':
                            self.assertTrue(raw.startswith('event: error\n'))
                            payload = {'type': 'error', 'sequence_number': 0, 'param': None,
                                       'code': detail['code'], 'message': detail['message'], 'retry_after': 15}
                            raw = raw.removeprefix('event: error\n')
                        else:
                            payload = {'error': dict(detail, retry_after=15)}
                        self.assertTrue(raw.startswith('data: '))
                        self.assertEqual(json.loads(raw.removeprefix('data: ')), payload)
                    finally:
                        finish.set()

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
        self.assertEqual(await r.read(),b'data: local\n\ndata: [DONE]\n\n')
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
