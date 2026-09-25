"""`ods dashboard-login` prints a one-time sign-in link from the Dashboard API."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "T" * 43


class StubDashboardApi(http.server.BaseHTTPRequestHandler):
    status = 200
    seen = []

    def log_message(self, *_args):
        return

    def do_POST(self):
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        body = json.dumps({"token": TOKEN, "fragment": "#ods-login=" + TOKEN, "expiresIn": 600}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipIf(os.name == "nt", "POSIX CLI")
class DashboardLoginCliTests(unittest.TestCase):
    def setUp(self):
        StubDashboardApi.status = 200
        StubDashboardApi.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StubDashboardApi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.install = Path(tempfile.mkdtemp())
        (self.install / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")

    def run_cli(self, env_lines, *args):
        (self.install / ".env").write_text("DASHBOARD_API_KEY=unit-test-key\n" + env_lines, encoding="utf-8")
        env = {**os.environ, "INSTALL_DIR": str(self.install), "NO_COLOR": "1",
               "ODS_DASHBOARD_API_URL": "http://127.0.0.1:%d" % self.server.server_address[1]}
        return subprocess.run(["bash", str(ROOT / "ods-cli"), "dashboard-login", *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_local_only_install_prints_a_fragment_link_and_the_local_note(self):
        result = self.run_cli("BIND_ADDRESS=127.0.0.1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(StubDashboardApi.seen, [("/api/auth/dashboard-session/link", "Bearer unit-test-key")])
        self.assertIn("#ods-login=" + TOKEN, result.stdout)
        self.assertIn("http://localhost:3001 never asks", result.stdout)
        # The token stays in the URL fragment, never a query string servers log.
        self.assertNotIn("?ods-login", result.stdout)

    def test_proxy_and_specific_bind_addresses_get_ready_links(self):
        proxy = self.install / "extensions/services/ods-proxy"
        proxy.mkdir(parents=True)
        (proxy / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        result = self.run_cli("BIND_ADDRESS=100.64.0.7\nODS_DEVICE_NAME=kitchen\nDASHBOARD_PORT=3005\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("http://dashboard.kitchen.local/#ods-login=" + TOKEN, result.stdout)
        self.assertIn("http://100.64.0.7:3005/#ods-login=" + TOKEN, result.stdout)
        self.assertIn("every browser signs in", result.stdout)

    def test_refusals_and_arguments_fail_without_printing_a_link(self):
        StubDashboardApi.status = 403
        refused = self.run_cli("")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("HTTP 403", refused.stderr)
        self.assertNotIn(TOKEN, refused.stdout)
        extra = self.run_cli("", "--now")
        self.assertEqual(extra.returncode, 1)
        self.assertIn("Usage: ods dashboard-login", extra.stderr)


if __name__ == "__main__":
    unittest.main()
