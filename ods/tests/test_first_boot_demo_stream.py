"""Run the guided demo with real curl/jq against a local OpenAI HTTP fixture."""
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
TOKEN = 'data: {"choices":[{"delta":{"content":"one"}}]}\n\n'


@unittest.skipUnless(shutil.which("curl") and shutil.which("jq"), "curl and jq are required")
class DemoStreamTest(unittest.TestCase):
    def test_stream_completion_and_failures(self):
        cases = {
            "complete": (200, TOKEN + "data: [DONE]\n\n", True),
            "crlf": (200, (TOKEN + "data: [DONE]\n\n").replace("\n", "\r\n"), True),
            "incomplete": (200, TOKEN, False),
            "invalid_json": (200, TOKEN + "data: {broken}\n\ndata: [DONE]\n\n", False),
            "provider_error": (200, TOKEN + 'data: {"error":{"message":"failed"}}\n\ndata: [DONE]\n\n', False),
            "usage_trailer": (200, TOKEN + 'data: {"choices":[],"usage":{"completion_tokens":1}}\n\ndata:[DONE]\n\n', True),
            "no_tokens": (200, "data: [DONE]\n\n", False),
            "http_error": (503, '{"error":"model unavailable"}', False),
            "truncated_http": (200, TOKEN + "data: [DONE]\n\n", False),
        }
        for name, (status, stream, success) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                self.run_demo(Path(directory), name, status, stream.encode(), success)

    def run_demo(self, directory, name, status, stream, success):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                streaming = payload.get("stream", False)
                body = stream if streaming else b'{"choices":[{"message":{"content":"fixture response"}}]}'
                self.send_response(status if streaming else 200)
                self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
                self.send_header("Content-Length", str(len(body) + (20 if streaming and name == "truncated_http" else 0)))
                self.end_headers()
                try:
                    # Exercise transport chunks which do not align with SSE lines.
                    for offset in range(0, len(body), 7):
                        self.wfile.write(body[offset:offset + 7])
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        script = directory / "scripts" / "first-boot-demo.sh"
        script.parent.mkdir()
        shutil.copy2(ROOT / "scripts" / "first-boot-demo.sh", script)
        binaries = directory / "bin"
        binaries.mkdir()
        clear = binaries / "clear"
        clear.write_text("#!/bin/sh\nexit 0\n")
        clear.chmod(0o755)
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            url = f"http://127.0.0.1:{server.server_port}"
            environment = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "TERM": "xterm",
                           "LLM_MODEL": "fixture-model", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            environment.update({key: url for key in ("LLM_URL", "WHISPER_URL", "PIPER_URL", "N8N_URL", "WEBUI_URL")})
            try:
                result = subprocess.run(["bash", str(script), "--quick"], cwd=directory, env=environment,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, timeout=15)
            finally:
                server.shutdown()
                worker.join(3)
        self.assertEqual(len(requests), 3, result.stdout)
        self.assertTrue(requests[-1]["stream"])
        self.assertTrue(all(row["model"] == "fixture-model" for row in requests))
        if success:
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("one", result.stdout)
            self.assertIn("Streaming works!", result.stdout)
            self.assertIn("Demo Complete!", result.stdout)
            self.assertNotIn("[DONE]", result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Streaming demo did not complete", result.stdout)
            self.assertNotIn("Streaming works!", result.stdout)
            self.assertNotIn("Demo Complete!", result.stdout)


if __name__ == "__main__":
    unittest.main()
