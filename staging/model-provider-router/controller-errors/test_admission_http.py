import json
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import test_server as f


class AdmissionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):f.Tests.setUpClass()
    @classmethod
    def tearDownClass(cls):f.Tests.tearDownClass()
    def setUp(self):
        self.fixture=f.Tests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f.remote.READY_WAIT=0
    def call(self,path):
        req=urllib.request.Request('http://127.0.0.1:'+str(f.Tests.front.server_port)+path,data=b'{}',headers={'Authorization':'Bearer api_key','Content-Type':'application/json'})
        try:r=urllib.request.urlopen(req,timeout=3)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.load(r),r.headers
    def assert_error(self,status,code,retry):
        for path in ('/v1/chat/completions','/v1/responses'):
            got,body,headers=self.call(path)
            self.assertEqual(got,status,body);self.assertEqual(body['error']['code'],code)
            self.assertEqual(headers.get('Retry-After'),retry)
            self.assertEqual(headers.get('Cache-Control'),'no-store')
            self.assertFalse(f.remote.ACTIVE and code!='concurrency_limit_exceeded')
    def test_active_generation_is_busy(self):
        f.remote.ACTIVE=True
        self.assert_error(429,'concurrency_limit_exceeded','15')
    def test_loading_job_is_loading(self):
        f.remote.JOB={'status':'running','action':'start'}
        self.assert_error(503,'resource_not_ready','15')
    def test_profile_transition(self):
        f.remote.JOB={'status':'running','action':'stop'}
        self.assert_error(503,'resource_not_ready','15')
    def test_resource_conflict_has_no_automatic_retry(self):
        with patch.object(f.remote,'blocked_reason',return_value='GPU reserved'):
            self.assert_error(409,'resource_busy',None)
    def test_failed_start_hides_private_diagnostics(self):
        f.remote.JOB={'status':'failed','action':'start','at':time.time(),'error':'PRIVATE SECRET PATH'}
        with patch.object(f.remote,'ready',return_value=False):
            self.assert_error(502,'initialization_failed','60')
            _,body,_=self.call('/v1/chat/completions')
            self.assertNotIn('PRIVATE',json.dumps(body))
    def test_gpu_lease_conflict(self):
        with patch.object(f.remote,'acquire_gpu',side_effect=RuntimeError('GPU lease owned')):
            self.assert_error(429,'concurrency_limit_exceeded','15')
    def test_desktop_profile_conflict(self):
        with patch.object(f.remote,'ownership',return_value={'mode':'headless'}):
            self.assert_error(409,'resource_conflict',None)

if __name__=='__main__':unittest.main()
