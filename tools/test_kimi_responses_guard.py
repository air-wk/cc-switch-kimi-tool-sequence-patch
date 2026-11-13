import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = Path.home() / '.codex' / 'tools' / 'kimi_responses_guard.py'

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

class MockHandler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        type(self).requests.append({'path': self.path, 'body': body, 'authorization': self.headers.get('Authorization')})
        if self.path == '/v1/error':
            payload = b'{"error":"mock upstream error"}'
            self.send_response(418)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.loads(body)
        if payload.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b'data: {"type":"first"}\n\n')
            self.wfile.flush()
            time.sleep(0.15)
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            return
        output = json.dumps({'ok': True}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(output)))
        self.end_headers()
        self.wfile.write(output)

class GuardIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = ThreadingHTTPServer(('127.0.0.1', 0), MockHandler)
        cls.mock_port = cls.mock.server_address[1]
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        cls.guard_port = free_port()
        cls.temp = tempfile.TemporaryDirectory()
        env = os.environ.copy()
        env['KIMI_GUARD_STATE_PATH'] = str(Path(cls.temp.name) / 'state.json')
        env['KIMI_GUARD_LOG_PATH'] = str(Path(cls.temp.name) / 'guard.log')
        cls.process = subprocess.Popen([
            sys.executable, str(SCRIPT), 'serve', '--port', str(cls.guard_port),
            '--upstream', f'http://127.0.0.1:{cls.mock_port}/v1',
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(80):
            try:
                status, _, _ = cls.request('GET', '/health')
                if status == 200:
                    break
            except OSError:
                pass
            if cls.process.poll() is not None:
                out, err = cls.process.communicate()
                raise RuntimeError(f'Guard exited: {out} {err}')
            time.sleep(0.1)
        else:
            raise RuntimeError('Guard health check timed out')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.communicate()
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.temp.cleanup()

    def setUp(self):
        MockHandler.requests.clear()
        self.request('POST', '/control', {'enabled': True})

    @classmethod
    def request(cls, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        conn = http.client.HTTPConnection('127.0.0.1', cls.guard_port, timeout=5)
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-only'}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = response.status, data, dict(response.getheaders())
        conn.close()
        return result

    def responses_payload(self, model='k3.1-pro', missing=False):
        items = [
            {'type': 'function_call', 'call_id': 'a', 'name': 'view_image', 'arguments': '{}'},
            {'type': 'function_call', 'call_id': 'b', 'name': 'view_image', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'a', 'output': 'ok'},
            {'type': 'message', 'role': 'developer', 'content': 'resize'},
        ]
        if not missing:
            items.append({'type': 'function_call_output', 'call_id': 'b', 'output': 'ok'})
        items.append({'type': 'message', 'role': 'user', 'content': 'next'})
        return {'model': model, 'input': items}

    def test_responses_reordered_and_auth_forwarded(self):
        status, _, _ = self.request('POST', '/v1/responses', self.responses_payload())
        self.assertEqual(status, 200)
        received = json.loads(MockHandler.requests[0]['body'])
        self.assertEqual([x['type'] for x in received['input']], [
            'function_call', 'function_call', 'function_call_output',
            'function_call_output', 'message', 'message'])
        self.assertEqual(received['input'][4]['role'], 'developer')
        self.assertEqual(MockHandler.requests[0]['authorization'], 'Bearer local-test-only')

    def test_chat_reordered(self):
        payload = {'model': 'kimi-k3.1-pro', 'messages': [
            {'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'x', 'arguments': '{}'}}]},
            {'role': 'developer', 'content': 'resize'},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'ok'},
            {'role': 'user', 'content': 'next'},
        ]}
        status, _, _ = self.request('POST', '/v1/chat/completions', payload)
        self.assertEqual(status, 200)
        received = json.loads(MockHandler.requests[0]['body'])
        self.assertEqual([x['role'] for x in received['messages']], ['assistant', 'tool', 'developer', 'user'])

    def test_non_kimi_and_disabled_guard_are_byte_identical(self):
        payload = self.responses_payload(model='deepseek-v4-1-flash')
        original = json.dumps(payload).encode()
        status, _, _ = self.request('POST', '/v1/responses', payload)
        self.assertEqual(status, 200)
        self.assertEqual(MockHandler.requests[0]['body'], original)
        MockHandler.requests.clear()
        self.request('POST', '/control', {'enabled': False})
        payload = self.responses_payload()
        original = json.dumps(payload).encode()
        status, _, _ = self.request('POST', '/v1/responses', payload)
        self.assertEqual(status, 200)
        self.assertEqual(MockHandler.requests[0]['body'], original)

    def test_missing_output_is_local_422_not_forwarded(self):
        status, body, _ = self.request('POST', '/v1/responses', self.responses_payload(missing=True))
        self.assertEqual(status, 422)
        self.assertIn(b'invalid_tool_sequence', body)
        self.assertEqual(MockHandler.requests, [])

    def test_stream_and_upstream_error_are_forwarded(self):
        payload = {'model': 'k3.1-pro', 'input': [], 'stream': True}
        status, body, headers = self.request('POST', '/v1/responses', payload)
        self.assertEqual(status, 200)
        self.assertIn('text/event-stream', headers.get('Content-Type'))
        self.assertEqual(body, b'data: {"type":"first"}\n\ndata: [DONE]\n\n')
        status, body, _ = self.request('POST', '/v1/error', {'model': 'k3.1-pro'})
        self.assertEqual(status, 418)
        self.assertIn(b'mock upstream error', body)

if __name__ == '__main__':
    unittest.main(verbosity=2)
