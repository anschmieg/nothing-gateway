"""CPU-only HTTP tests: auth, destructive controls, busy state, streaming and ownership."""
import importlib.util
import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('remote', pathlib.Path(__file__).with_name('server.py'))
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.seen = {'body': body, 'local_only': self.headers.get('X-Heretic-Local-Only')}
        if body.get('stream'):
            time.sleep(.12)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"OK"}}]}')


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        remote.BACKEND = cls.backend.server_address
        remote.HEARTBEAT = .03
        cls.front = remote.BoundedServer(('127.0.0.1', 0), remote.Handler)
        for server in [cls.backend, cls.front]:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in [cls.backend, cls.front]:
            server.shutdown(); server.server_close()

    def setUp(self):
        remote.ACTIVE = False
        remote.JOB = None
        remote.READY_WAIT = .25
        remote.WAITERS = 0
        self.patches = [patch.object(remote, 'acquire_gpu', side_effect=tempfile.TemporaryFile),
                        patch.object(remote, 'blocked_reason', return_value=None),
                        patch.object(remote, 'key', side_effect=lambda name: name),
                        patch.object(remote, 'selected', return_value={'profile': 'heretic-112k', 'context': 114688}),
                        patch.object(remote, 'ready', return_value=True),
                        patch.object(remote, 'worker_busy', return_value=False),
                        patch.object(remote, 'stack_running', return_value=False),
                        patch.object(remote, 'desktop_active', return_value=True),
                        patch.object(remote, 'ownership', return_value=None)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def call(self, path, body=None, credential='api_key', headers=None):
        request_headers = {'Authorization': 'Bearer ' + credential, **(headers or {})}
        req = urllib.request.Request(f'http://127.0.0.1:{self.front.server_port}' + path,
                                     data=None if body is None else json.dumps(body).encode(), headers=request_headers)
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        try:
            return response.status, response.read()
        finally:
            response.close()

    def test_video_lease_blocks_inference(self):
        with patch.object(remote, 'acquire_gpu', side_effect=RuntimeError('GPU owned by video')):
            code, body = self.call('/v1/chat/completions', {'model': 'heretic'})
            self.assertEqual(code, 429)
            self.assertEqual(json.loads(body)['error']['code'], 'concurrency_limit_exceeded')
            self.assertFalse(remote.ACTIVE)

    def test_resources_block_start(self):
        with patch.object(remote, 'blocked_reason', return_value='A Steam game is running.'), \
             patch.object(remote, 'transition') as transition:
            code, body = self.call('/control/start', {'mode': 'desktop'}, credential='admin_key')
            self.assertEqual(code, 409)
            self.assertEqual(json.loads(body)['code'], 'resources_busy')
            transition.assert_not_called()

    def test_resources_block_inference(self):
        with patch.object(remote, 'blocked_reason', return_value='GPU is busy.'):
            code, body = self.call('/v1/chat/completions', {'model': 'heretic'})
            self.assertEqual(code, 409)
            self.assertFalse(remote.ACTIVE)

    def test_unauthenticated(self):
        self.assertEqual(self.call('/v1/models', credential='wrong')[0], 401)

    def test_api_key_cannot_control(self):
        self.assertEqual(self.call('/control/start', {'mode': 'desktop'})[0], 401)

    def test_models_while_unloaded(self):
        with patch.object(remote, 'ready', return_value=False):
            code, data = self.call('/v1/models')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(data)['data'][0]['context_window'], 94208)

    def test_headless_requires_explicit_flag(self):
        for mode in ['headless', 'vision']:
            with patch.object(remote, 'transition') as transition:
                self.assertEqual(self.call('/control/start', {'mode': mode, 'close_desktop': True}, 'admin_key')[0], 409)
                transition.assert_not_called()

    def test_auto_preserves_desktop(self):
        with patch.object(remote, 'desktop_active', return_value=True), patch.object(remote, 'transition') as transition:
            self.assertEqual(self.call('/control/start', {'mode': 'auto'}, 'admin_key')[0], 202)
            transition.assert_called_once_with('start', 'auto', False)

    def test_auto_uses_already_headless_pc(self):
        with patch.object(remote, 'desktop_active', return_value=False), patch.object(remote, 'transition') as transition:
            self.assertEqual(self.call('/control/start', {'mode': 'auto'}, 'admin_key')[0], 202)
            transition.assert_called_once_with('start', 'auto', False)

    def test_transition_blocked_during_generation(self):
        remote.ACTIVE = True
        self.assertEqual(self.call('/control/stop', {}, 'admin_key')[0], 409)

    def test_transition_blocked_during_local_generation(self):
        with patch.object(remote, 'worker_busy', return_value=True):
            self.assertEqual(self.call('/control/stop', {}, 'admin_key')[0], 409)

    def test_unloaded_rejects_generation(self):
        with patch.object(remote, 'ready', return_value=False), patch.object(remote, 'auto_transition'):
            self.assertEqual(self.call('/v1/chat/completions', {'model': 'heretic'})[0], 503)

    def test_shared_cold_start_and_stream(self):
        from concurrent.futures import ThreadPoolExecutor
        loaded = threading.Event()
        def start():
            time.sleep(.05)
            with remote.LOCK:
                loaded.set()
                remote.JOB = {'status': 'complete', 'at': time.time()}
        remote.READY_WAIT = 2
        with patch.object(remote, 'ready', side_effect=loaded.is_set), patch.object(remote, 'auto_transition', side_effect=start) as startup:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda i: self.call('/v1/chat/completions', {'model': 'heretic', 'stream': True}), range(4)))
            self.assertTrue(all(code == 200 and b'[DONE]' in data for code, data in results))
            startup.assert_called_once()
        self.assertEqual(remote.WAITERS, 0)

    def test_timeout_does_not_start_second_job(self):
        with patch.object(remote, 'ready', return_value=False), patch.object(remote, 'auto_transition') as startup:
            start = time.monotonic()
            for _ in range(2):
                code, data = self.call('/v1/chat/completions', {})
                self.assertEqual(code, 503)
                self.assertEqual(json.loads(data)['error']['type'], 'service_unavailable')
            self.assertLess(time.monotonic() - start, 1.5)
            startup.assert_called_once()

    def test_failed_start_cooldown(self):
        def fail():
            with remote.LOCK:
                remote.JOB = {'status': 'failed', 'at': time.time()}
        with patch.object(remote, 'ready', return_value=False), patch.object(remote, 'auto_transition', side_effect=fail) as startup:
            for _ in range(2):
                self.assertEqual(self.call('/v1/responses', {})[0], 502)
            startup.assert_called_once()

    def test_automatic_transition_never_switches_existing_stack(self):
        with patch.object(remote, 'stack_running', return_value=True), patch.object(remote.session_policy, 'prepare') as prepare, patch.object(remote, 'run') as run:
            remote.transition('start', 'auto', False, automatic=True)
            self.assertEqual(remote.JOB['status'], 'failed')
            prepare.assert_not_called()
            run.assert_not_called()

    def test_denial_has_retry_after_and_no_start(self):
        import http.client
        with patch.object(remote, 'blocked_reason', return_value='busy'), patch.object(remote, 'auto_transition') as startup:
            conn = http.client.HTTPConnection(*self.front.server_address)
            conn.request('POST', '/v1/chat/completions', b'{}', {'Authorization': 'Bearer api_key'})
            response = conn.getresponse()
            self.assertEqual(response.status, 409)
            self.assertIsNone(response.getheader('Retry-After'))
            response.read(); conn.close()
            startup.assert_not_called()

    def test_unknown_model(self):
        self.assertEqual(self.call('/v1/chat/completions', {'model': 'cloud'})[0], 400)

    def test_private_default_and_alias(self):
        code, _ = self.call('/v1/chat/completions', {'model': 'heretic', 'messages': []})
        self.assertEqual(code, 200)
        self.assertEqual(self.backend.seen['local_only'], '1')
        self.assertEqual(self.backend.seen['body']['model'], 'iq3')

    def test_hosted_repair_disabled(self):
        self.call('/v1/chat/completions', {'messages': []}, headers={'X-Heretic-Allow-Hosted-Repair': '1'})
        self.assertEqual(self.backend.seen['local_only'], '1')

    def test_stream_heartbeats_before_delayed_headers(self):
        code, data = self.call('/v1/responses', {'stream': True, 'input': 'hello'})
        self.assertEqual(code, 200)
        self.assertIn(b': inference in progress', data)
        self.assertIn(b'data: [DONE]', data)
        self.assertFalse(remote.ACTIVE)

    def test_never_stop_unowned_stack(self):
        with patch.object(remote, 'run') as run:
            remote.stop_owned()
            run.assert_not_called()

    def test_never_stop_independently_changed_profile(self):
        with patch.object(remote, 'ownership', return_value={'mode': 'headless', 'profile': 'heretic-160k-headless'}), patch.object(remote, 'run') as run:
            with self.assertRaises(RuntimeError):
                remote.stop_owned()
            run.assert_not_called()


    def test_server_auto_uses_unoccupied_screen_without_force_logout(self):
        with patch.object(remote.session_policy, 'prepare', return_value={'mode': 'server'}), \
             patch.object(remote.session_policy, 'profile_reason', return_value=None), \
             patch.object(remote, 'ready', return_value=False), \
             patch.object(remote, 'run') as run, patch.object(remote, 'save_owner') as save:
            remote.transition('start', 'auto', False)
            self.assertEqual(remote.JOB['status'], 'complete')
            self.assertEqual(run.call_args.args[0], [remote.STACK, 'start', 'heretic-160k-headless'])
            save.assert_called_once_with('headless', 'server', False)

    def test_desktop_auto_uses_small_profile(self):
        with patch.object(remote.session_policy, 'prepare', return_value={'mode': 'desktop'}), \
             patch.object(remote.session_policy, 'profile_reason', return_value=None), \
             patch.object(remote, 'ready', return_value=False), \
             patch.object(remote, 'run') as run, patch.object(remote, 'save_owner') as save:
            remote.transition('start', 'auto', False)
            self.assertEqual(remote.JOB['status'], 'complete')
            self.assertEqual(run.call_args.args[0], [remote.STACK, 'start', 'heretic-92k'])
            save.assert_called_once_with('desktop', 'desktop', False)

    def test_budget_failure_restores_greeter_without_loading(self):
        state = {'mode': 'server', 'stopped_greeter': True}
        with patch.object(remote.session_policy, 'prepare', return_value=state), \
             patch.object(remote.session_policy, 'profile_reason', return_value='GPU budget exceeded'), \
             patch.object(remote.session_policy, 'release') as release, \
             patch.object(remote, 'ready', return_value=False), patch.object(remote, 'run') as run:
            remote.transition('start', 'auto', False)
            self.assertEqual(remote.JOB['status'], 'failed')
            run.assert_not_called()
            release.assert_called_once_with(state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
