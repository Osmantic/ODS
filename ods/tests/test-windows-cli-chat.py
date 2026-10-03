"""Exercise the installed Windows chat command on the HTTP wire."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "Windows PowerShell required")
class WindowsChatTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.catalog = {"data": [{"id": "user.selected", "checkpoint": "selected.gguf"}]}
        seen = self.requests
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, None, self.headers.get("Authorization")))
                self.reply({"version": "10.0.0"} if self.path.endswith("/health") else fixture.catalog)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, json.loads(body.decode("utf-8")), self.headers.get("Authorization")))
                self.reply({"choices": [{"message": {"content": "fixture reply"}}]})

            def reply(self, value):
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode("utf-8"))

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.directory = tempfile.TemporaryDirectory(prefix="ods chat ")
        self.addCleanup(self.directory.cleanup)

    def chat(self, values, message="hello"):
        values = {"GPU_BACKEND": "amd", "LLM_BACKEND": "lemonade",
                  "AMD_INFERENCE_PORT": str(self.server.server_port), **values}
        Path(self.directory.name, ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
        env = {**os.environ, "ODS_HOME": self.directory.name}
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "installers/windows/ods.ps1"), "chat", message],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_enabled_switchboard_uses_current_alias_and_saved_credentials(self):
        self.chat({"ODS_MODEL_SWITCHBOARD": "enabled", "LITELLM_PORT": str(self.server.server_port),
                   "LITELLM_KEY": "fixture-gateway", "LEMONADE_MODEL": "other-model"})
        self.assertEqual(len(self.requests), 1)
        path, body, authorization = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "ods/current")
        self.assertEqual(authorization, "Bearer fixture-gateway")

    def test_legacy_lemonade_uses_persisted_model_and_unicode_prompt(self):
        message = "Xin ch\u00e0o \U0001f600"
        self.chat({"LEMONADE_MODEL": "user.selected", "LITELLM_LEMONADE_API_KEY": "fixture-native"}, message)
        path, body, authorization = self.requests[-1]
        self.assertEqual(path, "/api/v1/chat/completions")
        self.assertEqual(body["model"], "user.selected")
        self.assertEqual(body["messages"][0]["content"], message)
        self.assertEqual(authorization, "Bearer fixture-native")

    def test_legacy_lemonade_resolves_catalog_when_model_id_is_absent(self):
        self.chat({"GGUF_FILE": "selected.gguf"})
        self.assertEqual(self.requests[0][0], "/api/v1/models")
        self.assertEqual(self.requests[-1][1]["model"], "user.selected")

    def test_legacy_lemonade_supports_explicit_server_key(self):
        self.chat({"LEMONADE_MODEL": "user.selected", "LEMONADE_API_KEY": "fixture-server"})
        self.assertEqual(self.requests[-1][2], "Bearer fixture-server")

    def test_missing_lemonade_identity_refuses_implicit_default_load(self):
        output = self.chat({})
        self.assertEqual(self.requests, [])
        self.assertIn(b"LEMONADE_MODEL", output)

    def test_legacy_lemonade_retains_version_specific_catalog_fallback(self):
        self.catalog = {"data": []}
        self.chat({"GGUF_FILE": "selected.gguf"})
        self.assertEqual(self.requests[0][0], "/api/v1/models")
        self.assertEqual(self.requests[-1][1]["model"], "extra.selected.gguf")

    def test_legacy_llama_retains_default_model(self):
        self.chat({"GPU_BACKEND": "nvidia", "LLM_BACKEND": "llama-server",
                   "OLLAMA_PORT": str(self.server.server_port)})
        self.assertEqual(self.requests[-1][0], "/v1/chat/completions")
        self.assertEqual(self.requests[-1][1]["model"], "default")

    def test_enabled_switchboard_without_key_refuses_to_bypass_route(self):
        output = self.chat({"ODS_MODEL_SWITCHBOARD": "enabled",
                            "LITELLM_PORT": str(self.server.server_port)})
        self.assertEqual(self.requests, [])
        self.assertIn(b"LITELLM_KEY", output)


if __name__ == "__main__":
    unittest.main()
