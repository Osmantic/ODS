#!/usr/bin/env python3
"""Exercise the actual Caddy proxy with isolated loopback HTTP fixtures.

Run with --caddy-bin /path/to/caddy (the pinned compose image's binary works).
No live ODS services, credentials, Docker network, or host ports are modified.
"""

import argparse
import contextlib
import http.client
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CADDY = None


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        if self.server.is_auth:
            # Exercise proxy admission independently of the existing signed-
            # cookie unit tests. The literal below is a fixture, not a secret.
            ok = self.headers.get("Cookie") == "ods-session=fixture-valid"
            self.send_response(200 if ok else 401)
            self.end_headers()
        elif self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
        elif self.path == "/api/protected":
            self.send_response(401)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Hermes fixture")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()


@contextlib.contextmanager
def upstream(is_auth):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server.requests = []
    server.is_auth = is_auth
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProxyAccess(unittest.TestCase):
    @contextlib.contextmanager
    def proxy(self, gate=None, auth_available=True):
        with upstream(False) as hermes, upstream(True) as auth, tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            directory = Path(tmp)
            text = (ROOT / "extensions/services/hermes-proxy/Caddyfile").read_text()
            text = text.replace(":9120 {", f"http://127.0.0.1:{port} {{")
            text = text.replace("/srv/auth-required", str(directory).replace("\\", "/"))
            (directory / "index.html").write_text("Owner-card fixture")
            config = directory / "Caddyfile"
            config.write_text(text)
            env = dict(os.environ)
            env.pop("HERMES_REQUIRE_OWNER_CARD", None)
            if gate is not None:
                env["HERMES_REQUIRE_OWNER_CARD"] = gate
            env["HERMES_PROXY_UPSTREAM"] = f"127.0.0.1:{hermes.server_port}"
            env["ODS_AUTH_UPSTREAM"] = f"127.0.0.1:{auth.server_port}" if auth_available else "127.0.0.1:1"
            env["XDG_DATA_HOME"] = str(directory / "data")
            env["XDG_CONFIG_HOME"] = str(directory / "config")
            with (directory / "caddy.log").open("w+") as log:
                process = subprocess.Popen([CADDY, "run", "--config", str(config), "--adapter", "caddyfile"], env=env, stdout=log, stderr=log)
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read())
                        try:
                            if self.request(port, "/health")[0] == 200:
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("Caddy did not become ready")
                    yield port, hermes.requests, auth.requests
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def request(self, port, path="/", headers=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read() if response.status != 101 else b""
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_default_opens_without_owner_card_or_auth_backend(self):
        with self.proxy(auth_available=False) as (port, calls, auth):
            self.assertEqual(self.request(port)[0], 200)
            self.assertEqual(self.request(port, headers={"Cookie": "ods-session=forged"})[0], 200)
            self.assertEqual(self.request(port, "/api/protected")[0], 401)
            self.assertEqual(len(calls), 3)
            self.assertEqual(auth, [])

    def test_explicit_false_opens_without_session(self):
        with self.proxy("false") as (port, calls, auth):
            self.assertEqual(self.request(port)[0], 200)
            self.assertEqual(len(calls), 1)
            self.assertEqual(auth, [])

    def test_opt_in_rejects_missing_and_forged_cookies_without_forwarding(self):
        with self.proxy("true") as (port, calls, auth):
            for method, headers in [("GET", {}), ("POST", {}), ("GET", {"Cookie": "ods-session=forged"})]:
                status, response_headers, _ = self.request(port, headers=headers, method=method)
                self.assertEqual(status, 303)
                self.assertEqual(response_headers.get("Location"), "/auth/required")
            self.assertEqual(calls, [])
            self.assertEqual(len(auth), 3)
            for path in ("/health", "/healthz", "/auth/required"):
                self.assertEqual(self.request(port, path)[0], 200)
            self.assertEqual(len(auth), 3)

    def test_opt_in_accepts_verified_session_but_preserves_upstream_api_auth(self):
        with self.proxy("true") as (port, calls, auth):
            headers = {"Cookie": "ods-session=fixture-valid"}
            self.assertEqual(self.request(port, headers=headers)[0], 200)
            self.assertEqual(self.request(port, "/api/protected", headers=headers)[0], 401)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(auth), 2)

    def test_opt_in_fails_closed_when_auth_backend_is_unavailable(self):
        with self.proxy("true", auth_available=False) as (port, calls, _):
            self.assertEqual(self.request(port)[0], 502)
            self.assertEqual(calls, [])

    def test_websocket_upgrade_in_both_modes(self):
        for gate in (None, "true"):
            with self.subTest(gate=gate), self.proxy(gate) as (port, calls, auth):
                headers = {"Connection": "Upgrade", "Upgrade": "websocket", "Cookie": "ods-session=fixture-valid", "Sec-WebSocket-Key": "fixture-key", "Sec-WebSocket-Version": "13"}
                self.assertEqual(self.request(port, "/api/ws", headers=headers)[0], 101)
                self.assertEqual(len(calls), 1)
                if gate:
                    self.assertEqual(len(auth), 1)
                    forwarded = {key.lower(): value for key, value in auth[0][1].items()}
                    self.assertNotIn("upgrade", forwarded)
                    self.assertNotIn("sec-websocket-key", forwarded)
                else:
                    self.assertEqual(auth, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--caddy-bin", required=True)
    args, remaining = parser.parse_known_args()
    CADDY = str(Path(args.caddy_bin).resolve(strict=True))
    unittest.main(argv=[__file__, *remaining])
