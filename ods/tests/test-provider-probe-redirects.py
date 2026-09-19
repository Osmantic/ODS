"""Real HTTP redirect responses must not move a credential-bearing probe."""
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from remote_provider.probe import ProbeError, probe_provider_route


class ProbeRedirectTest(unittest.TestCase):
    def test_default_transport_refuses_redirects(self):
        captures = []
        redirect_status = 302

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/capture':
                    captures.append(self.headers.get('Authorization'))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"data": []}')
                    return
                self.send_response(redirect_status)
                self.send_header('Location', f'http://localhost:{self.server.server_port}/capture')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        route = {'enabled': True, 'transport': 'direct', 'provider': {'baseUrl': f'http://127.0.0.1:{server.server_port}'}}
        try:
            # Only allow the local fixture past address policy. The production
            # opener, HTTP parsing and redirect handling are exercised unchanged.
            with patch('remote_provider.probe.validate_direct_provider_resolution', return_value=['127.0.0.1']):
                for redirect_status in (301, 302, 303, 307, 308):
                    with self.subTest(status=redirect_status):
                        with self.assertRaises(ProbeError) as caught:
                            probe_provider_route(route, provider_secret='redirect-test-key')
                        self.assertEqual(caught.exception.status, 502)
                        self.assertEqual(caught.exception.code, 'provider_redirect_rejected')
                        self.assertEqual(captures, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
