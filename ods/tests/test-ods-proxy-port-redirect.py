#!/usr/bin/env python3
"""Exercise ODS's real Caddy redirect and Compose port contract on loopback.

Run with --caddy-bin from the shipped image. No installed services or LAN
listeners are changed; Compose is only used to render configuration.
"""

import argparse
import contextlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CADDY = None


class ProxyRedirectTests(unittest.TestCase):
    @contextlib.contextmanager
    def proxy(self, published_port=None, device=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            # Keep all production handlers. Only relocate listeners to a
            # private loopback port; the external port remains independent.
            text = (ROOT / "extensions/services/ods-proxy/Caddyfile").read_text(encoding="utf-8")
            text = text.replace("\n{\n", f"\n{{\n\thttp_port {port}\n\tdefault_bind 127.0.0.1\n", 1)
            text = text.replace("\n:80 {", f"\n:{port} {{")
            config = directory / "Caddyfile"
            config.write_text(text, encoding="utf-8")
            env = dict(os.environ)
            for key in ("ODS_PROXY_PORT", "ODS_DEVICE_NAME"):
                env.pop(key, None)
            if published_port is not None:
                env["ODS_PROXY_PORT"] = str(published_port)
            if device is not None:
                env["ODS_DEVICE_NAME"] = device
            env["XDG_DATA_HOME"] = str(directory / "data")
            env["XDG_CONFIG_HOME"] = str(directory / "config")
            with (directory / "caddy.log").open("w+", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [CADDY, "run", "--config", str(config), "--adapter", "caddyfile"],
                    env=env, stdout=log, stderr=log,
                )
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read())
                        try:
                            if self.request(port, "/health", "probe.invalid")[0] == 200:
                                break
                        except OSError:
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail("Caddy did not become ready")
                    yield port
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def request(self, port, path, host):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request("GET", path, headers={"Host": host})
            response = connection.getresponse()
            body = response.read()
            return response.status, response.getheader("Location"), body
        finally:
            connection.close()

    def assert_redirect(self, port, published_port, host="office.local", path="/projects?a=1", device="office"):
        status, location, _body = self.request(port, path, host)
        self.assertEqual(status, 302)
        target = urlsplit(location)
        self.assertEqual(target.scheme, "http")
        self.assertEqual(target.hostname, f"chat.{device}.local")
        self.assertEqual(target.port or 80, published_port)
        self.assertEqual(target.path + (f"?{target.query}" if target.query else ""), path)

    def test_custom_port_preserves_path_and_query(self):
        with self.proxy(18080, "office") as port:
            self.assert_redirect(port, 18080, host="office.local:18080")

    def test_configured_port_is_used_without_host_port(self):
        with self.proxy(18080, "office") as port:
            self.assert_redirect(port, 18080)

    def test_host_port_does_not_override_configuration(self):
        with self.proxy(18080, "office") as port:
            self.assert_redirect(port, 18080, host="office.local:9999")

    def test_escaped_uri_is_preserved(self):
        with self.proxy(65535, "office") as port:
            self.assert_redirect(port, 65535, path="/some%20file?a=1%26b&lang=vi")

    def test_default_port_and_device_are_compatible(self):
        with self.proxy() as port:
            self.assert_redirect(port, 80, host="ods.local", path="/", device="ods")

    def test_explicit_default_port_is_compatible(self):
        with self.proxy(80, "office") as port:
            self.assert_redirect(port, 80)

    def test_other_hosts_are_not_redirected(self):
        with self.proxy(18080, "office") as port:
            status, location, _body = self.request(port, "/", "foreign.local")
            self.assertEqual(status, 200)
            self.assertIsNone(location)

    def test_compose_passes_the_actual_published_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            base = directory / "base.yaml"
            base.write_text(json.dumps({"services": {
                name: {"image": "busybox:1.37"}
                for name in ("dashboard", "dashboard-api", "open-webui")
            }}), encoding="utf-8")
            empty_env = directory / "empty.env"
            empty_env.write_text("", encoding="utf-8")
            for value in (None, "80", "18080"):
                with self.subTest(port=value):
                    env = dict(os.environ)
                    env.pop("ODS_PROXY_PORT", None)
                    if value is not None:
                        env["ODS_PROXY_PORT"] = value
                    result = subprocess.run([
                        "docker", "compose", "--env-file", str(empty_env),
                        "-f", str(base), "-f", str(ROOT / "extensions/services/ods-proxy/compose.yaml"),
                        "config", "--format", "json",
                    ], env=env, cwd=directory, text=True, capture_output=True, check=True)
                    service = json.loads(result.stdout)["services"]["ods-proxy"]
                    published = next(item["published"] for item in service["ports"] if item["target"] == 80)
                    self.assertEqual(service["environment"].get("ODS_PROXY_PORT"), str(published))
                    self.assertEqual(str(published), value or "80")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--caddy-bin", required=True)
    args, remaining = parser.parse_known_args()
    CADDY = str(Path(args.caddy_bin).resolve(strict=True))
    unittest.main(argv=[__file__, *remaining])
