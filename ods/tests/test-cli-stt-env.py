#!/usr/bin/env python3
"""Exercise real STT CLI commands against a local model-cache HTTP fixture."""

import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ODS = Path(__file__).resolve().parents[1]


class SttEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ods stt env ")
        self.root = Path(self.directory.name)
        (self.root / "docker-compose.base.yml").write_text("services: {}\n")
        self.requests = []
        self.cached = True
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                fixture.requests.append(("GET", self.path))
                code = 200 if self.path == "/v1/models" or fixture.cached else 404
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                fixture.requests.append(("POST", self.path))
                fixture.cached = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def command(self, *arguments):
        environment = {**os.environ, "INSTALL_DIR": str(self.root), "NO_COLOR": "1",
                       "AUDIO_STT_MODEL": "ambient/ignored", "WHISPER_PORT": "1"}
        return subprocess.run(["bash", str(ODS / "ods-cli"), "stt", *arguments],
                              env=environment, capture_output=True, text=True, timeout=20)

    def write_env(self, model, port, newline="\n"):
        (self.root / ".env").write_bytes(
            f"AUDIO_STT_MODEL={model}{newline}WHISPER_PORT={port}{newline}".encode())

    def test_quoted_config_for_current_status_and_download(self):
        for quote in ("'", '"'):
            for newline in ("\n", "\r\n"):
                with self.subTest(quote=quote, newline=repr(newline)):
                    self.write_env(f"{quote}example/whisper{quote} # selected model",
                                   f"{quote}{self.server.server_port}{quote} # host port", newline)
                    current = self.command("current")
                    self.assertEqual(current.returncode, 0, current.stderr)
                    self.assertEqual(current.stdout, f"STT model: example/whisper\nWhisper URL: http://127.0.0.1:{self.server.server_port}\n")
                    self.requests.clear()
                    status = self.command("status")
                    self.assertEqual(status.returncode, 0, status.stderr)
                    self.assertIn(("GET", "/v1/models/example%2Fwhisper"), self.requests)
                    self.cached = False
                    result = self.command("download")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(("POST", "/v1/models/example%2Fwhisper"), self.requests)
                    self.assertIn("Downloaded and cached: example/whisper", result.stdout)

    def test_explicit_model_retains_precedence(self):
        self.write_env("'example/default'", str(self.server.server_port))
        result = self.command("status", "override/whisper")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests[-1], ("GET", "/v1/models/override%2Fwhisper"))

    def test_empty_or_missing_config_retains_legacy_defaults(self):
        for content in (None, "AUDIO_STT_MODEL=''\nWHISPER_PORT=\"\"\n", "# no voice settings\n"):
            path = self.root / ".env"
            if content is not None:
                path.write_text(content)
            result = self.command("current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "STT model: Systran/faster-whisper-base\nWhisper URL: http://127.0.0.1:9000\n")

    def test_config_text_is_never_executed(self):
        marker = self.root / "executed"
        literal = f"$(touch {marker})"
        self.write_env("'" + literal + "'", "9000")
        result = self.command("current")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STT model: " + literal + "\n", result.stdout)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
