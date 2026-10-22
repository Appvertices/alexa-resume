"""Independent HTTP probes; these are not an official SDK interoperability test."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer

from server import make_handler


class ProtocolRegressions(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), make_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, payload, sid=None, headers=None, method='POST', raw=None):
        h = {'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream',
             'MCP-Protocol-Version': '2025-11-25'}
        if sid:
            h['MCP-Session-Id'] = sid
        h.update(headers or {})
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        c.request(method, '/mcp', raw if raw is not None else json.dumps(payload), h)
        r = c.getresponse()
        body = r.read()
        result = (r.status, json.loads(body) if body and r.getheader('Content-Type', '').startswith('application/json') else body,
                  dict(r.getheaders()))
        c.close()
        return result

    def init(self, version='2025-11-25', headers=None):
        status, data, h = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': version, 'capabilities': {},
                                               'clientInfo': {'name': 'wire-regression', 'version': '1'}}}, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(data['result']['protocolVersion'], '2025-11-25')
        sid = h['MCP-Session-Id']
        self.assertEqual(self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid)[:2], (202, b''))
        return sid, h

    def state(self, sid):
        s, data, _ = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                'params': {'name': 'routine_state', 'arguments': {}}}, sid)
        self.assertEqual(s, 200)
        return json.loads(data['result']['content'][0]['text'])

    def test_notification_and_client_response_are_accepted_without_body(self):
        sid, _ = self.init()
        for message in [
            {'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 999}},
            {'jsonrpc': '2.0', 'method': 'notifications/custom'},
            {'jsonrpc': '2.0', 'id': 999, 'result': {}},
            {'jsonrpc': '2.0', 'id': 999, 'error': {'code': -32601, 'message': 'Unknown'}},
        ]:
            with self.subTest(message=message):
                self.assertEqual(self.post(message, sid)[:2], (202, b''))

    def test_notification_cannot_execute_a_mutating_tool(self):
        sid, _ = self.init()
        self.post({'jsonrpc': '2.0', 'method': 'tools/call', 'params': {
            'name': 'routine_turn', 'arguments': {'text': 'done', 'version': 0}}}, sid)
        self.assertEqual(self.state(sid)['completed'], 0)

    def test_ids_and_envelopes_are_validated(self):
        sid, _ = self.init()
        messages = [{'jsonrpc': '2.0', 'id': value, 'method': 'ping'}
                    for value in [True, None, [], {}, 1.5]]
        messages += [{'jsonrpc': '2.0', 'id': 1, 'method': None},
                     {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'result': {}},
                     {'jsonrpc': '2.0', 'id': 1, 'result': {}, 'error': {'code': 1, 'message': 'x'}},
                     []]
        for message in messages:
            with self.subTest(message=message):
                status, data, _ = self.post(message, sid)
                self.assertEqual(status, 400)
                self.assertEqual(data['error']['code'], -32600)

    def test_parse_error_has_jsonrpc_error_envelope(self):
        status, data, _ = self.post(None, raw='{bad')
        self.assertEqual(status, 400)
        self.assertEqual(data, {'jsonrpc': '2.0', 'id': None,
                                'error': {'code': -32700, 'message': 'Parse error.'}})

    def test_session_header_is_required_even_with_cookie(self):
        sid, h = self.init()
        cookie = h.get('Set-Cookie', f'local_session_{self.server.server_port}={sid}').split(';')[0]
        status, _, response_headers = self.post({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'},
                                                headers={'Cookie': cookie})
        self.assertEqual(status, 400)
        self.assertNotIn('MCP-Session-Id', response_headers)

    def test_initialize_negotiates_supported_version(self):
        self.init(version='2025-03-26')

    def test_initialize_requires_capabilities_and_client_info(self):
        status, data, h = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                   'params': {'protocolVersion': '2025-11-25'}})
        self.assertEqual(data['error']['code'], -32602)
        self.assertNotIn('MCP-Session-Id', h)

    def test_initialize_with_a_cookie_still_creates_independent_session(self):
        sid, h = self.init()
        cookie = h.get('Set-Cookie', f'local_session_{self.server.server_port}={sid}').split(';')[0]
        other, _ = self.init(headers={'Cookie': cookie})
        self.assertNotEqual(other, sid)

    def test_delete_invalidates_session(self):
        sid, _ = self.init()
        self.assertEqual(self.post(None, sid, method='DELETE')[0], 200)
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}, sid)[0], 404)

    def test_expired_session_is_not_replaced(self):
        sid, _ = self.init()
        import time
        later = time.monotonic() + 3601
        with patch('server.time.monotonic', return_value=later):
            status, _, h = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}, sid)
        self.assertEqual(status, 404)
        self.assertNotIn('MCP-Session-Id', h)

    def test_wrong_protocol_header_rejected_without_mutation(self):
        sid, _ = self.init()
        status, _, _ = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'routine_turn', 'arguments': {'text': 'done', 'version': 0}}}, sid,
            headers={'MCP-Protocol-Version': '2024-11-05'})
        self.assertEqual(status, 400)
        self.assertEqual(self.state(sid)['completed'], 0)

    def test_delete_validates_origin_missing_and_expired_session(self):
        sid, _ = self.init()
        self.assertEqual(self.post(None, sid, method='DELETE', headers={'Origin': 'https://untrusted.example'})[0], 403)
        self.assertEqual(self.post(None, method='DELETE')[0], 400)
        self.assertEqual(self.post(None, 'unknown', method='DELETE')[0], 404)
        self.assertEqual(self.state(sid)['completed'], 0)

    def test_unknown_method_and_execution_error_are_distinct(self):
        sid, _ = self.init()
        _, data, _ = self.post({'jsonrpc': '2.0', 'id': 'unknown', 'method': 'unknown'}, sid)
        self.assertEqual(data['error']['code'], -32601)
        self.assertEqual(data['id'], 'unknown')
        _, data, _ = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'routine_turn', 'arguments': {'text': 'done', 'version': 99}}}, sid)
        self.assertTrue(data['result']['isError'])
        self.assertEqual(self.state(sid)['completed'], 0)

    def test_tool_maxlength_counts_whitespace_before_mutation(self):
        sid, _ = self.init()
        _, data, _ = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'routine_turn', 'arguments': {'text': 'done' + ' ' * 397, 'version': 0}}}, sid)
        self.assertTrue(data['result']['isError'])
        self.assertEqual(self.state(sid)['completed'], 0)

    def test_lifecycle_requires_initialized_notification_before_tools(self):
        _, _, h = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
        sid = h['MCP-Session-Id']
        _, data, _ = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, sid)
        self.assertEqual(data['error']['code'], -32002)
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'}, sid)[1]['result'], {})
        self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid)
        self.assertEqual(self.state(sid)['version'], 0)

    def test_ping_during_initialization(self):
        s, data, _ = self.post({'jsonrpc': '2.0', 'id': 0, 'method': 'ping'})
        self.assertEqual((s, data.get('result')), (200, {}))

    def test_get_405_is_supported_json_only_behavior(self):
        sid, _ = self.init()
        status, _, h = self.post(None, sid, method='GET')
        self.assertEqual(status, 405)
        self.assertIn('POST', h.get('Allow', ''))

    def test_request_named_initialized_does_not_hang_client(self):
        sid, _ = self.init()
        status, data, _ = self.post({'jsonrpc': '2.0', 'id': 10, 'method': 'notifications/initialized'}, sid)
        self.assertEqual(status, 200)
        self.assertEqual(data['error']['code'], -32601)

    def test_invalid_accept_cannot_exhaust_session_capacity(self):
        for _ in range(105):
            self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                                       headers={'Accept': 'application/json'})[0], 406)
        self.init()

    def test_checkpoint_roundtrip_between_independent_clients(self):
        a, _ = self.init()
        b, _ = self.init()
        self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'routine_turn', 'arguments': {'text': 'done', 'version': 0}}}, a)
        checkpoint = self.state(a)['checkpoint']
        self.assertEqual(self.state(b)['completed'], 0)
        _, result, _ = self.post({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'routine_restore', 'arguments': {'checkpoint': checkpoint, 'version': 0, 'confirmed': True}}}, b)
        self.assertFalse(result['result']['isError'])
        self.assertEqual(self.state(b)['completed'], 1)

