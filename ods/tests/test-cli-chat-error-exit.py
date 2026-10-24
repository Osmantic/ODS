#!/usr/bin/env python3
"""Real HTTP responses must drive useful process exit codes in both CLIs."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ODS = Path(__file__).resolve().parents[1]


class ChatErrorExitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods chat response ")
        self.root = Path(self.temporary.name)
        self.assertEqual(self.root.resolve().parent, Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temporary.cleanup)
        self.status = 200
        self.response = {"choices": [{"message": {"content": "fixture answer"}}]}
        self.requests = []
        self.api_key = ""
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                authorization = self.headers.get("Authorization", "")
                fixture.requests.append((self.path, body, authorization))
                if fixture.api_key and authorization != "Bearer " + fixture.api_key:
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"fixture authentication required"}}')
                    return
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(fixture.response).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        (self.root / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
        self.configure()

    def configure(self, native=False):
        backend = "amd" if native else "nvidia"
        (self.root / ".env").write_text(
            f"ODS_MODE=local\nGPU_BACKEND={backend}\nLLM_BACKEND=llama-server\n"
            f"OLLAMA_PORT={self.server.server_port}\nAMD_INFERENCE_PORT={self.server.server_port}\n"
            f"ODS_NATIVE_LLAMA_PORT={self.server.server_port}\nLLAMA_SERVER_API_KEY={self.api_key}\n",
            encoding="utf-8",
        )

    def macos(self):
        if os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"):
            self.skipTest("POSIX Bash and jq are required")
        return subprocess.run(["bash", str(ODS / "installers/macos/ods-macos.sh"), "chat", "fixture request"],
                              env={**os.environ, "ODS_HOME": str(self.root)},
                              text=True, capture_output=True, timeout=15)

    def windows(self):
        powershell = shutil.which("powershell.exe") if os.name == "nt" else None
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        # Run the complete CLI and endpoint resolver against test-only .env data.
        source = ODS / "installers/windows/ods.ps1"
        return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(source), "chat", "fixture request"],
                              text=True, encoding="utf-8", errors="replace", capture_output=True,
                              env={**os.environ, "ODS_HOME": str(self.root)}, timeout=15)

    def exercise(self, platform):
        invoke = getattr(self, platform)
        for case in ("answer", "http-error", "api-error", "missing-content", "empty-content",
                     "whitespace-content", "unicode-whitespace-content", "non-string-content"):
            with self.subTest(case=case):
                self.status = 503 if case == "http-error" else 200
                if case in ("http-error", "api-error"):
                    self.response = {"error": {"message": "fixture backend unavailable"}}
                elif case == "missing-content":
                    self.response = {"choices": []}
                else:
                    content = {"answer": "  fixture answer  ", "empty-content": "",
                               "whitespace-content": " \t\n ", "unicode-whitespace-content": "\u00a0\u2003",
                               "non-string-content": ["fixture answer"]}[case]
                    self.response = {"choices": [{"message": {"content": content}}]}
                self.requests.clear()
                result = invoke()
                self.assertEqual(len(self.requests), 1, result.stdout + result.stderr)
                self.assertEqual(self.requests[0][0], "/v1/chat/completions")
                self.assertEqual(self.requests[0][1]["messages"][0]["content"], "fixture request")
                if case == "answer":
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("  fixture answer  ", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    diagnostic = result.stdout + result.stderr
                    expected = ("Chat request failed" if case == "http-error" else
                                "fixture backend unavailable" if case == "api-error" else
                                "No assistant text in the response")
                    self.assertIn(expected, diagnostic)
                    self.assertNotIn("fixture answer", result.stdout)
                self.assertEqual(self.requests[0][2], "")

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"),
                     "POSIX Bash and jq are required")
    def test_macos_command_exit_matches_http_response(self):
        self.exercise("macos")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell is required")
    def test_windows_command_exit_matches_http_response(self):
        self.exercise("windows")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell is required")
    def test_windows_native_chat_uses_the_configured_endpoint_credential(self):
        self.api_key = "ods-test-only-not-a-secret"
        self.configure(native=True)
        result = self.windows()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture answer", result.stdout)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0][2], "Bearer " + self.api_key)
        self.assertNotIn(self.api_key, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
