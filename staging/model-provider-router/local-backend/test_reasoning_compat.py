import copy
import unittest
from reasoning_compat import normalize_reasoning


class ReasoningCompatibilityTests(unittest.TestCase):
    def test_high_mapping_preserves_prompt_tools_cache_and_input(self):
        body={'model':'iq3','reasoning_effort':'high','messages':[{'role':'system','content':'Stable prefix'}], 'tools':[{'type':'function','function':{'name':'lookup'}}], 'cache_prompt':True}
        before=copy.deepcopy(body)
        result=normalize_reasoning(body)
        self.assertEqual(result,dict(before,reasoning_effort='xhigh'))
        self.assertEqual(body,before)
        self.assertEqual(normalize_reasoning(result),result)

    def test_responses_and_template_overrides(self):
        body={'reasoning':{'effort':'high','summary':'auto'},'chat_template_kwargs':{'reasoning_effort':'high','enable_thinking':True}}
        self.assertEqual(normalize_reasoning(body),{'reasoning':{'effort':'xhigh','summary':'auto'},'chat_template_kwargs':{'reasoning_effort':'xhigh','enable_thinking':True}})

    def test_other_efforts_preserved(self):
        for effort in ('low','medium','xhigh',None):
            body={'reasoning_effort':effort}
            self.assertEqual(normalize_reasoning(body),body)


class GatewayReasoningTests(unittest.TestCase):
    def test_http_paths_send_native_effort(self):
        import json
        import urllib.request
        from unittest.mock import patch
        import test_gateway as fixtures
        import server
        fixtures.GatewayTests.setUpClass()
        try:
            client=fixtures.GatewayTests()
            original=urllib.request.urlopen
            sent=[]
            def capture(request,*args,**kwargs):
                if request.full_url.startswith(server.BACKEND):
                    sent.append(json.loads(request.data))
                return original(request,*args,**kwargs)
            with patch.object(server.urllib.request,'urlopen',side_effect=capture):
                for path in ('/v1/chat/completions','/v1/responses'):
                    for stream in (False,True):
                        body={'model':'iq3','stream':stream,'reasoning_effort':'high','messages':[{'role':'system','content':'Stable prefix'},{'role':'user','content':'Hello'}]}
                        result=client.call(path,body)
                        self.assertEqual(result[0],200)
                        self.assertEqual(sent[-1]['reasoning_effort'],'xhigh')
                        self.assertEqual(sent[-1]['messages'],body['messages'])
        finally:
            fixtures.GatewayTests.tearDownClass()

if __name__=='__main__':unittest.main()
