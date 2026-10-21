#!/usr/bin/env python3
"""Exercise the installed CLI against distinct local and gateway HTTP servers."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ODS = Path(__file__).resolve().parents[1]


class ChatRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods chat route ")
        self.root = Path(self.temporary.name)
        self.install = self.root / "install"
        self.install.mkdir()
        (self.install / "docker-compose.base.yml").write_text("services: {}\n")
        self.foreign = self.root / "foreign"
        self.foreign.mkdir()
        (self.foreign / "sentinel").write_text("retain\n")
        self.blocked = self.root / "bin"
        self.blocked.mkdir()
        for command in ("docker", "sudo", "systemctl", "service", "launchctl"):
            path = self.blocked / command
            path.write_text("#!/bin/sh\necho unexpected-host-command >&2\nexit 99\n")
            path.chmod(0o755)
        self.key = "fixture-" + "g" * 24
        self.requests = []
        self.gateway_status = 200
        self.completion_status = 200
        self.servers = []
        self.threads = []
        fixture = self
        for route in ("local", "gateway"):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def respond(self, payload):
                    status = fixture.gateway_status if self.server.route == "gateway" else 200
                    if self.path.endswith("completions"):
                        status = fixture.completion_status
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(payload).encode())

                def do_GET(self):
                    fixture.requests.append((self.server.route, self.path,
                                             self.headers.get("Authorization"), None))
                    self.respond({"data": [{"id": "local.gguf"}]})

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    fixture.requests.append((self.server.route, self.path,
                                             self.headers.get("Authorization"), body))
                    self.respond({"choices": [{"message": {"content": "fixture reply"}}]})

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.route = route
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append(server)
            self.threads.append(thread)
        self.local, self.gateway = (server.server_port for server in self.servers)

    def tearDown(self):
        for server, thread in zip(self.servers, self.threads):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.temporary.cleanup()

    def command(self, values, *arguments):
        config = {"OLLAMA_PORT": str(self.local), "LITELLM_PORT": str(self.gateway),
                  "LITELLM_KEY": self.key, **values}
        (self.install / ".env").write_text("".join(f"{key}={value}\n" for key, value in config.items()))
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        env = {**os.environ, "INSTALL_DIR": str(self.install), "ODS_HOME": str(self.install),
               "ODS_INSTALL_DIR": str(self.foreign), "NO_COLOR": "1",
               "PATH": str(self.blocked) + os.pathsep + os.environ["PATH"]}
        # Do not inherit model-selection state from the QA host.
        for key in ("ODS_MODE", "ODS_MODEL_SWITCHBOARD", "EXTERNAL_LLM_URL", "NATIVE_LLM_BASE_URL",
                    "LLAMA_SERVER_PORT", "LITELLM_MASTER_KEY"):
            env.pop(key, None)
        result = subprocess.run(["bash", str(ODS / "ods-cli"), *arguments], env=env,
                                cwd=self.foreign, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15)
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("unexpected-host-command", result.stderr)
        return result

    def assert_gateway(self, values, model="ods/current", arguments=None):
        result = self.command(values, *(arguments or ("chat", "Xin chào 🌏")))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture reply", result.stdout)
        self.assertTrue(self.requests)
        self.assertTrue(all(request[0] == "gateway" for request in self.requests), self.requests)
        self.assertTrue(all(request[2] == "Bearer " + self.key for request in self.requests))
        sent = next(request[3] for request in self.requests if request[1].endswith("completions"))
        self.assertEqual(sent["model"], model)
        if arguments is None:
            self.assertEqual(sent["messages"][0]["content"], "Xin chào 🌏")

    def test_enabled_switchboard_uses_authenticated_public_alias(self):
        self.assert_gateway({"ODS_MODEL_SWITCHBOARD": "enabled"})

    def test_cloud_route_uses_gateway_without_local_probe(self):
        self.assert_gateway({"ODS_MODE": "cloud", "ODS_MODEL_SWITCHBOARD": "observe"}, model="default")

    def test_external_and_host_native_routes_use_gateway(self):
        for values in ({"EXTERNAL_LLM_URL": "https://provider.invalid/v1"},
                       {"NATIVE_LLM_BASE_URL": "http://127.0.0.1:1"}):
            with self.subTest(values=values):
                self.requests.clear()
                self.assert_gateway(values, model="default")

    def test_explicit_model_is_preserved(self):
        self.assert_gateway({"ODS_MODEL_SWITCHBOARD": "enabled"}, model="explicit/model",
                            arguments=("chat", "message", "explicit/model"))

    def test_benchmark_uses_the_same_selected_route(self):
        self.assert_gateway({"ODS_MODEL_SWITCHBOARD": "enabled"}, arguments=("benchmark",))

    def test_missing_gateway_key_refuses_before_any_request(self):
        result = self.command({"ODS_MODEL_SWITCHBOARD": "enabled", "LITELLM_KEY": ""}, "chat", "hello")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LITELLM_KEY", result.stderr)
        self.assertEqual(self.requests, [])

    def test_supported_master_key_alias_authenticates_to_gateway(self):
        self.assert_gateway({"ODS_MODEL_SWITCHBOARD": "enabled", "LITELLM_KEY": "",
                             "LITELLM_MASTER_KEY": self.key})

    def test_gateway_failure_does_not_fall_back_to_local_model(self):
        self.gateway_status = 503
        result = self.command({"ODS_MODEL_SWITCHBOARD": "enabled"}, "chat", "hello")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.requests)
        self.assertTrue(all(request[0] == "gateway" for request in self.requests), self.requests)

    def test_unsuccessful_completion_is_a_failure_even_with_content_in_its_body(self):
        self.completion_status = 503
        result = self.command({"ODS_MODEL_SWITCHBOARD": "enabled"}, "chat", "hello")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("fixture reply", result.stdout)
        self.assertTrue(all(request[0] == "gateway" for request in self.requests), self.requests)

    def test_legacy_local_route_keeps_model_discovery_and_sends_no_gateway_key(self):
        result = self.command({"ODS_MODEL_SWITCHBOARD": "observe"}, "chat", "hello")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([request[0] for request in self.requests], ["local", "local"])
        self.assertTrue(all(request[2] is None for request in self.requests))
        self.assertEqual(self.requests[-1][3]["model"], "local.gguf")


if __name__ == "__main__":
    unittest.main()
