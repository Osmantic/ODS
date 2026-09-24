#!/usr/bin/env python3
"""Exercise the actual CLI and HTTP/filesystem boundaries for speech output."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import wave

ODS = Path(__file__).resolve().parents[1]


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods speech ")
        self.root = Path(self.temp.name)
        self.install = self.root / "installation"
        self.install.mkdir()
        (self.install / "docker-compose.base.yml").write_text("services: {}\n")
        self.requests = []
        self.status = 200
        self.content_type = "audio/pcm"
        self.declared_length = None
        self.before_reply = None
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00\xff\xff" * 120)
        self.wav = buffer.getvalue()
        self.audio = b"\x01\x00\xff\xff" * 120
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                fixture.requests.append((self.path, None))
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"voices":["af_heart","af_sky+af_bella"]}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append((self.path, body))
                if fixture.before_reply is not None:
                    fixture.before_reply()
                self.send_response(fixture.status)
                self.send_header("Content-Type", fixture.content_type)
                if fixture.declared_length is not None:
                    self.send_header("Content-Length", str(fixture.declared_length))
                if fixture.status == 302:
                    self.send_header("Location", "/unexpected-redirect")
                self.end_headers()
                self.wfile.write(fixture.audio)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (self.install / ".env").write_bytes(f"TTS_PORT='{self.server.server_port}' # configured\r\n".encode())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def command(self, *args, stdin=None):
        return subprocess.run(["bash", str(ODS / "ods-cli"), "tts", *args], cwd=self.root,
                              env={**os.environ, "INSTALL_DIR": str(self.install), "NO_COLOR": "1"},
                              text=True, input=stdin, capture_output=True, timeout=20)

    def test_voice_discovery_uses_the_shipped_endpoint(self):
        result = self.command("voices")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"voices": ["af_heart", "af_sky+af_bella"]})
        self.assertEqual(self.requests, [("/v1/audio/voices", None)])

    def test_quoted_text_and_relative_output_remain_literal(self):
        text = 'He said "hello".\nTài liệu \\ $not_shell'
        output = self.root / 'âm "quoted" output.wav'
        result = self.command("speak", text, output.name, "--voice", "af_sky+af_bella")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), self.wav)
        self.assertEqual(self.requests, [("/v1/audio/speech", {
            "model": "kokoro", "input": text, "voice": "af_sky+af_bella",
            "response_format": "pcm", "stream": False,
        })])
        self.assertEqual(list(self.root.glob(".ods-speech-*")), [])

    def test_stdin_and_default_voice(self):
        result = self.command("speak", "-", "stdin.wav", stdin="A local report.\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests[0][1]["input"], "A local report.\n")
        self.assertEqual(self.requests[0][1]["voice"], "af_heart")
        self.assertEqual((self.root / "stdin.wav").read_bytes(), self.wav)

    def test_existing_output_is_never_replaced(self):
        output = self.root / "existing.wav"
        output.write_bytes(b"operator data")
        result = self.command("speak", "Hello", output.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"operator data")
        self.assertEqual(self.requests, [])

    def test_output_created_during_generation_wins(self):
        output = self.root / "racing.wav"
        self.before_reply = lambda: output.write_bytes(b"concurrent writer")
        result = self.command("speak", "Hello", output.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"concurrent writer")
        self.assertEqual(list(self.root.glob(".ods-speech-*")), [])

    def test_failed_or_invalid_audio_does_not_publish_a_file(self):
        for status, body in ((503, self.audio), (302, self.audio), (200, b""), (200, self.audio[:-1])):
            with self.subTest(status=status, length=len(body)):
                self.status, self.audio = status, body
                self.requests.clear()
                result = self.command("speak", "Hello", "failed.wav")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "failed.wav").exists())
                self.assertEqual(list(self.root.glob(".ods-speech-*")), [])
                self.assertEqual(len(self.requests), 1)

    def test_wrong_content_type_and_incomplete_http_body_are_rejected(self):
        self.content_type = "application/json"
        self.assertNotEqual(self.command("speak", "Hello", "failed.wav").returncode, 0)
        self.content_type = "audio/pcm"
        self.declared_length = len(self.audio) + 100
        self.assertNotEqual(self.command("speak", "Hello", "failed.wav").returncode, 0)
        self.assertFalse((self.root / "failed.wav").exists())
        self.assertEqual(list(self.root.glob(".ods-speech-*")), [])

    def test_invalid_arguments_do_not_contact_the_service(self):
        for args in (("speak",), ("speak", " ", "blank.wav"), ("speak", "Hello", "missing/output.wav")):
            with self.subTest(args=args):
                self.assertNotEqual(self.command(*args).returncode, 0)
                self.assertEqual(self.requests, [])

    def test_invalid_configured_port_does_not_contact_the_service(self):
        for value in ("0", "65536", "not-a-port"):
            with self.subTest(value=value):
                (self.install / ".env").write_text(f"TTS_PORT='{value}'\n")
                self.assertNotEqual(self.command("voices").returncode, 0)
                self.assertEqual(self.requests, [])

    def test_registered_completion_exposes_the_new_subcommands(self):
        script = '''source "$1"
test_cword=$2
_init_completion() { cword=$test_cword; cur=""; prev=tts; }
_ods_completion
printf '%s\\n' "${COMPREPLY[@]}"
'''
        for position, expected in ((1, {"tts"}), (2, {"voices", "speak"})):
            result = subprocess.run(["bash", "-c", script, "bash", str(ODS / "completions/ods-cli.bash"),
                                     str(position)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected <= set(result.stdout.split()))


if __name__ == "__main__":
    unittest.main()
