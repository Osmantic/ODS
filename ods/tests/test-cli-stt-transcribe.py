#!/usr/bin/env python3
"""Run the CLI through real curl multipart uploads to a local Speaches fixture."""

from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ODS = Path(__file__).resolve().parents[1]


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods transcription ")
        self.root = Path(self.temp.name)
        self.install = self.root / "installation"
        self.install.mkdir()
        (self.install / "docker-compose.base.yml").write_text("services: {}\n")
        self.requests = []
        self.status = 200
        self.audio = b"RIFF\x00\xff\x01audio payload\r\n"
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                message = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body)
                parts = {part.get_param("name", header="content-disposition"):
                         part.get_payload(decode=True) for part in message.iter_parts()}
                fixture.requests.append((self.path, parts))
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"text": "Xin chào\nsecond line"}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (self.install / ".env").write_text(
            f"WHISPER_PORT='{self.server.server_port}' # published port\n"
            'AUDIO_STT_MODEL="local/default" # configured model\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def command(self, *arguments):
        return subprocess.run(["bash", str(ODS / "ods-cli"), "stt", "transcribe", *arguments],
                              cwd=self.root,
                              env={**os.environ, "INSTALL_DIR": str(self.install), "NO_COLOR": "1"},
                              text=True, capture_output=True, timeout=20)

    def test_relative_filename_and_json_stdout(self):
        (self.root / "recording.wav").write_bytes(self.audio)
        result = self.command("recording.wav")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"text": "Xin chào\nsecond line"})
        self.assertEqual(self.requests, [("/v1/audio/transcriptions", {
            "file": self.audio, "model": b"local/default", "response_format": b"json"})])

    def test_curl_form_syntax_in_filename_and_model_stays_literal(self):
        for filename in ('clip, second;type=text.wav', 'âm "quote" \\ clip.wav', '-recording.wav'):
            with self.subTest(filename=filename):
                path = self.root / filename
                path.write_bytes(self.audio)
                result = self.command(str(path), "@not-a-file;type=text/plain")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.requests[-1][1]["file"], self.audio)
                self.assertEqual(self.requests[-1][1]["model"], b"@not-a-file;type=text/plain")

    def test_invalid_arguments_do_not_upload(self):
        for arguments in ((), ("missing.wav",), (str(self.root),), ("a", "b", "c")):
            with self.subTest(arguments=arguments):
                result = self.command(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("transcribe", result.stderr)
                self.assertEqual(self.requests, [])

    def test_http_failure_is_a_failed_command(self):
        (self.root / "recording.wav").write_bytes(self.audio)
        self.status = 422
        result = self.command("recording.wav")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("422", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
