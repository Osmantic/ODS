"""Read-only completion gate tests; fake commands never contact the live stack."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'installers/verify-wsl-portal.sh'


@unittest.skipUnless(os.name == 'posix', 'requires Bash')
class PortalReadiness(unittest.TestCase):
    def probe(self, *, service=0, health='{"status":"ok"}', http=0, port='3001'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root / 'bin'
            bindir.mkdir()
            (root / '.env').write_text(f'DASHBOARD_PORT={port}\nPRIVATE_VALUE=do-not-print\n')
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
        self.assertIn('http://localhost:4321', result.stdout)
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

    def test_invalid_or_executable_port_is_not_evaluated(self):
        for port in ('0', '65536', 'abc', '$(echo injected)'):
            with self.subTest(port=port):
                result, calls = self.probe(port=port)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('127.0.0.1', calls)


if __name__ == '__main__':
    unittest.main()
