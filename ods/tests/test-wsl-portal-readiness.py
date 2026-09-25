"""Read-only completion gate tests; fake commands never contact the live stack."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'installers/verify-wsl-portal.sh'


@unittest.skipUnless(os.name == 'posix', 'requires Bash')
class PortalReadiness(unittest.TestCase):
    def probe(self, *, service=0, health='{"status":"ok"}', http=0, port='3001', available=True,
              api_code=200, api_body=None, not_ready_first=0):
        served = {'count': 0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root / 'bin'
            bindir.mkdir()
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    valid = self.path == '/api/pixel/status' and self.headers.get('Authorization') == 'Bearer do-not-print'
                    served['count'] += 1
                    if valid and served['count'] <= not_ready_first:
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b'{"available":false,"state":"model_unavailable","detail":"Model is loading"}')
                        return
                    self.send_response(api_code if valid else 403)
                    if api_code == 302:
                        self.send_header('Location', 'http://127.0.0.1:1/do-not-follow')
                    self.end_headers()
                    self.wfile.write(api_body if api_body is not None else
                                     (b'{"available":true}' if available else b'{"available":false}'))

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            (root / '.env').write_text(f'DASHBOARD_PORT={port}\nDASHBOARD_API_PORT={server.server_port}\nDASHBOARD_API_KEY=do-not-print\n')
            commands = {
                'systemctl': '#!/bin/sh\nexit "$SERVICE_CODE"\n',
                'curl': '''#!/bin/sh
printf '%s\n' "$*" >> "$CALLS"
case "$*" in
  *--unix-socket*) printf '%s' "$HEALTH" ;;
  *) exit "$HTTP_CODE" ;;
esac
''',
            }
            for name, body in commands.items():
                target = bindir / name
                target.write_text(body)
                target.chmod(0o755)
            env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}',
                       SERVICE_CODE=str(service), HTTP_CODE=str(http), HEALTH=health,
                       CALLS=str(root / 'calls'))
            result = subprocess.run(['bash', str(SCRIPT), str(root)], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertNotIn('do-not-print', result.stdout + result.stderr)
            calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
            return result, calls

    def test_ready_uses_configured_port(self):
        result, calls = self.probe(port='4321')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('http://localhost:4321/pixel', result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], 'ODS_PORTAL_URL=http://localhost:4321/pixel')
        self.assertIn('/run/ods-pixel/pixel-ingress.sock', calls)
        self.assertIn('http://127.0.0.1:4321/', calls)

    def test_inactive_service_never_claims_success(self):
        result, calls = self.probe(service=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')

    def test_unhealthy_or_malformed_ingress_stops_before_dashboard(self):
        for health in ('{"status":"degraded"}', 'not json'):
            with self.subTest(health=health):
                result, calls = self.probe(health=health)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('127.0.0.1', calls)

    def test_http_failure_remains_failure(self):
        result, _ = self.probe(http=22)
        self.assertNotEqual(result.returncode, 0)

    def test_dashboard_without_available_agent_fails(self):
        result, _ = self.probe(available=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Portal API verification failed', result.stderr)

    def test_unavailable_reports_nonsecret_detail(self):
        result, _ = self.probe(not_ready_first=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Model is loading', result.stderr)
        self.assertIn('model_unavailable', result.stderr)

    def test_authentication_failure_names_the_key_without_printing_it(self):
        result, _ = self.probe(api_code=401)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rejected the installed DASHBOARD_API_KEY', result.stderr)

    def test_invalid_or_oversized_status_fails(self):
        for body in (b'not json', b'{"available":"true"}', b'x' * 65537):
            with self.subTest(size=len(body)):
                result, _ = self.probe(api_body=body)
                self.assertNotEqual(result.returncode, 0)

    def test_authentication_errors_and_redirects_fail(self):
        for code in (401, 403, 302):
            with self.subTest(code=code):
                result, _ = self.probe(api_code=code)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('do-not-follow', result.stdout + result.stderr)

    def test_invalid_or_executable_port_is_not_evaluated(self):
        for port in ('0', '65536', 'abc', '$(echo injected)'):
            with self.subTest(port=port):
                result, calls = self.probe(port=port)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('127.0.0.1', calls)


if __name__ == '__main__':
    unittest.main()
