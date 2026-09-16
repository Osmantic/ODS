"""Exercise the post-install validator's real HTTP/JSON inference boundary."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("curl") and shutil.which("jq"), "curl and jq are required")
class ValidateChatTest(unittest.TestCase):
    def test_chat_readiness_requires_generated_text(self):
        cases = {
            "text": ({"choices": [{"message": {"content": "OK"}}]}, True),
            "null": ({"choices": [{"message": {"content": None}, "finish_reason": "length"}]}, False),
            "empty": ({"choices": [{"message": {"content": ""}}]}, False),
            "whitespace": ({"choices": [{"message": {"content": " \n\t"}}]}, False),
            "error": ({"error": {"message": "invalid content in request"}}, False),
            "non_string": ({"choices": [{"message": {"content": 123}}]}, False),
            "not_json": (b"content unavailable", False),
            "quoted_model": ({"choices": [{"message": {"content": "OK"}}]}, True),
        }
        for name, (body, success) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                self.run_validator(Path(directory), name, body, success)

    def run_validator(self, directory, name, body, success):
        model = 'local "quoted" \\ model' if name == "quoted_model" else "fixture-model"
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, value, status=200):
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.respond({"data": [{"id": model, "object": "model"}]} if self.path == "/v1/models" else {})

            def do_POST(self):
                try:
                    payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                except json.JSONDecodeError:
                    self.respond({"error": "Invalid JSON"}, 400)
                    return
                received.append(payload)
                self.respond(body)

        for relative in ("scripts/validate.sh", "lib/service-registry.sh", "lib/python-cmd.sh", "lib/safe-env.sh",
                         "extensions/services/llama-server/manifest.yaml", "extensions/services/open-webui/manifest.yaml"):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        binaries = directory / "bin"
        binaries.mkdir()
        docker = binaries / "docker"
        docker.write_text("#!/bin/sh\nprintf 'fixture Up\\n'\n")
        docker.chmod(0o755)
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            environment = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                           "OLLAMA_PORT": str(server.server_port), "WEBUI_PORT": str(server.server_port),
                           "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            try:
                result = subprocess.run(["bash", str(directory / "scripts/validate.sh")],
                                        cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
            finally:
                server.shutdown()
                worker.join(3)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("ODS is ready!", result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("1 test(s) failed", result.stdout)
            self.assertNotIn("ODS is ready!", result.stdout)
        self.assertEqual(len(received), 1, result.stdout)
        self.assertEqual(received[0]["model"], model)
        self.assertEqual(received[0]["messages"], [{"role": "user", "content": "Say OK"}])


if __name__ == "__main__":
    unittest.main()
