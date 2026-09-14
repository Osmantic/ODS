#!/usr/bin/env python3
"""Local Kokoro client used by `ods tts`; publish complete WAV files only."""

import argparse
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import wave

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARACTERS = 10000


def request(port, path, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
    try:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        connection.request("GET" if payload is None else "POST", path, body=body,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Local TTS service returned HTTP {response.status}: {response.reason}")
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("TTS response exceeds the 64 MiB output limit")
        return content
    finally:
        connection.close()


def validate_wav(content):
    with wave.open(io.BytesIO(content), "rb") as audio:
        frames = audio.getnframes()
        expected = frames * audio.getnchannels() * audio.getsampwidth()
        if frames == 0 or len(audio.readframes(frames)) != expected:
            raise ValueError("TTS service returned an empty or truncated WAV file")


def publish_wav(output, content):
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".ods-speech-", delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            # Same-directory hard linking publishes atomically and cannot replace
            # a file created by another process while synthesis was in progress.
            os.link(temporary_path, output)
        finally:
            temporary_path.unlink()


def main():
    parser = argparse.ArgumentParser(prog="ods tts", description=__doc__)
    parser.add_argument("--port", type=int, default=8880, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("voices", help="Print available local voice IDs as JSON")
    speak = commands.add_parser("speak", help="Create a new WAV file from text")
    speak.add_argument("text", help="Text to synthesize, or - to read standard input")
    speak.add_argument("output", type=Path, help="New output WAV path; existing files are refused")
    speak.add_argument("--voice", default="af_heart", help="Kokoro voice ID or supported voice combination")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("TTS_PORT must be between 1 and 65535")
    if args.command == "voices":
        print(json.dumps(json.loads(request(args.port, "/v1/audio/voices")), ensure_ascii=False, indent=2))
        return

    text = sys.stdin.read(MAX_TEXT_CHARACTERS + 1) if args.text == "-" else args.text
    if not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
        parser.error("Speech text must contain 1 to 10,000 characters and cannot be blank")
    if not args.output.parent.is_dir():
        parser.error("The output directory does not exist")
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"Refusing to replace existing output: {args.output}")
    content = request(args.port, "/v1/audio/speech", {
        "model": "kokoro", "input": text, "voice": args.voice,
        "response_format": "wav", "stream": False,
    })
    validate_wav(content)
    publish_wav(args.output, content)
    print(args.output)


if __name__ == "__main__":
    main()
