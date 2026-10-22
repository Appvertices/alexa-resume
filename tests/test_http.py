import http.client
import json
import threading
import unittest
from http.server import HTTPServer
from server import make_handler

class HTTPContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=HTTPServer(("127.0.0.1",0),make_handler())
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)
    def request(self,path="/api/state",method="GET",body=None,headers=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=4)
        conn.request(method,path,body,headers or {})
        r=conn.getresponse();raw=r.read();h=dict(r.getheaders());conn.close()
        return r.status,raw,h
    def test_state_has_no_live_integration(self):
        status,raw,h=self.request()
        self.assertEqual(status,200);self.assertIn("version",json.loads(raw))
        self.assertIn("HttpOnly",h["Set-Cookie"]);self.assertIn("SameSite=Strict",h["Set-Cookie"])
    def test_origin_rejected(self):
        self.assertEqual(self.request(headers={"Origin":"https://untrusted.example"})[0],403)
    def test_host_rebinding_rejected(self):
        self.assertEqual(self.request(headers={"Host":"attacker.example"})[0],403)
    def test_missing_json_type_rejected(self):
        self.assertEqual(self.request("/api/action","POST",'{"action":"x"}')[0],415)
    def test_invalid_json_rejected(self):
        self.assertEqual(self.request("/api/action","POST","{bad",{"Content-Type":"application/json"})[0],400)
    def test_unknown_action_rejected(self):
        self.assertEqual(self.request("/api/action","POST",'{"action":"evil","version":0}',{"Content-Type":"application/json"})[0],400)
    def test_static_source_hidden(self):
        self.assertEqual(self.request("/domain.py")[0],404)
    def test_traversal_rejected(self):
        self.assertEqual(self.request("/../server.py")[0],404)
    def test_html_security_headers(self):
        s,raw,h=self.request("/")
        self.assertEqual(s,200);self.assertIn(b'<html lang="en">',raw)
        self.assertIn("connect-src 'self'",h["Content-Security-Policy"])
    def test_session_isolation(self):
        a=self.request();b=self.request()
        self.assertNotEqual(a[2]["Set-Cookie"],b[2]["Set-Cookie"])


class PublicHost(unittest.TestCase):
    """A configured public hostname (e.g. a Render demo) is accepted over HTTPS; everything else is still rejected."""
    @classmethod
    def setUpClass(cls):
        cls.server=HTTPServer(("127.0.0.1",0),make_handler("resume-demo.example"))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)
    def status(self,headers):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=4)
        conn.request("GET","/",headers=headers);r=conn.getresponse();r.read();conn.close();return r.status
    def test_public_host_and_https_origin_allowed(self):
        self.assertEqual(self.status({"Host":"resume-demo.example","Origin":"https://resume-demo.example"}),200)
    def test_other_hosts_and_plain_http_origin_rejected(self):
        self.assertEqual(self.status({"Host":"attacker.example"}),403)
        self.assertEqual(self.status({"Host":"resume-demo.example","Origin":"http://resume-demo.example"}),403)
        self.assertEqual(self.status({"Host":"resume-demo.example","Origin":"https://attacker.example"}),403)
