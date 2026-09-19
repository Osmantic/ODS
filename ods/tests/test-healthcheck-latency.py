"""Real CLI probes distinguish slow success from an operator latency budget."""
import json
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/healthcheck.py'


class LatencyBudgetTest(unittest.TestCase):
    def test_slow_success_and_existing_failure(self):
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                time.sleep(0.05)
                self.send_response(int(self.path[1:]))
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            for status, budget, expected in [(200, None, 0), (200, '10000', 0), (200, '1', 1), (503, '1', 1)]:
                with self.subTest(status=status, budget=budget):
                    args = [sys.executable, str(SCRIPT), f'http://127.0.0.1:{server.server_port}/{status}', '--retries', '0', '--json']
                    if budget:
                        args += ['--max-latency-ms', budget]
                    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload['status'], status)
                    self.assertEqual(payload['ok'], expected == 0)
                    self.assertEqual('latency budget exceeded' in payload['detail'], status == 200 and budget == '1')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_budgets_fail_before_connecting(self):
        for budget in ('0', '-1', 'nan', 'inf', '1.5'):
            with self.subTest(budget=budget):
                result = subprocess.run([sys.executable, str(SCRIPT), 'http://127.0.0.1:1', '--max-latency-ms', budget], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
