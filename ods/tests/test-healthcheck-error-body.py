"""Allowed HTTP error statuses must still satisfy the CLI body predicate."""
import json
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class HealthcheckBodyTest(unittest.TestCase):
    def test_allowed_error_status_does_not_skip_body_regex(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(int(self.path[1:]))
                self.end_headers()
                if self.path == '/504':
                    time.sleep(0.2)
                    return
                self.wfile.write(b'expected maintenance state')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            script = Path(__file__).resolve().parents[1] / 'scripts/healthcheck.py'
            for status in (401, 503, 504):
                for pattern, expected in [('maintenance', 0), ('ready', 1)]:
                    with self.subTest(status=status, pattern=pattern):
                        if status == 504:
                            expected = 1
                        result = subprocess.run([
                            sys.executable, str(script), f'http://127.0.0.1:{server.server_port}/{status}',
                            '--expect-status', str(status), '--expect-body-regex', pattern,
                            '--retries', '0', '--json', '--timeout', '0.1',
                        ], capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                        payload = json.loads(result.stdout)
                        self.assertEqual(payload['ok'], expected == 0)
                        self.assertEqual(payload['status'], status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
