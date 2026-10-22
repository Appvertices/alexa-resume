import http.client
import json
import threading
import unittest
from http.server import HTTPServer
from server import make_handler

class MCPWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=HTTPServer(('127.0.0.1',0),make_handler())
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)
    def rpc(self,method,params=None,sid=None,notify=False,protocol=True):
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
        if protocol:headers['MCP-Protocol-Version']='2025-11-25'
        if sid:headers['MCP-Session-Id']=sid
        body={'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notify:body['id']=1
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        c.request('POST','/mcp',json.dumps(body),headers);r=c.getresponse();data=r.read();h=dict(r.getheaders());c.close()
        return r.status,json.loads(data) if data else None,h
    def init(self):
        status,data,h=self.rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}},protocol=False)
        self.assertEqual(status,200);self.assertEqual(data['result']['protocolVersion'],'2025-11-25')
        sid=h['MCP-Session-Id'];self.assertEqual(self.rpc('notifications/initialized',sid=sid,notify=True)[:2],(202,None));return sid
    def test_full_session_without_browser_cookie(self):
        sid=self.init();tools=self.rpc('tools/list',sid=sid)[1]['result']['tools'];self.assertEqual(len(tools),4)
        result=self.rpc('tools/call',{'name':'routine_turn','arguments':{'text':'done','version':0}},sid)[1]['result']
        self.assertFalse(result['isError']);self.assertEqual(json.loads(result['content'][0]['text'])['completed'],1)
        state=self.rpc('tools/call',{'name':'routine_state','arguments':{}},sid)[1]['result']
        self.assertEqual(json.loads(state['content'][0]['text'])['completed'],1)
    def test_unknown_session_rejected(self):
        self.assertEqual(self.rpc('tools/list',sid='missing')[0],404)
    def test_negotiated_session_supplies_missing_protocol_version(self):
        self.assertEqual(self.rpc('tools/list',sid=self.init(),protocol=False)[0],200)
    def test_schema_rejects_boolean_version_and_extra_arguments(self):
        sid=self.init()
        for args in [{'text':'done','version':False},{'text':'done','version':0,'untrusted':'extra'}]:
            result=self.rpc('tools/call',{'name':'routine_turn','arguments':args},sid)[1]
            self.assertEqual(result['error']['code'],-32602)
