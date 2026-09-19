"""Actual Kokoro CPU synthesis through the public ODS CLI."""

import array
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ("ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.4@sha256:"
         "c8812546d358cbfd6a5c4087a28795b2b001d8e32d7a322eedd246e6bc13cb55")


@pytest.mark.skipif(os.environ.get("ODS_TEST_TTS_DOCKER") != "1",
                    reason="Opt-in exact-image CPU speech synthesis")
def test_cli_creates_complete_audible_wav(tmp_path):
    name = "ods-tts-test-" + uuid.uuid4().hex[:10]
    install = tmp_path / "installation"
    install.mkdir()
    recipe = install / "docker-compose.base.yml"
    shutil.copyfile(ROOT / "extensions/services/tts/compose.yaml", recipe)
    overlay = install / "isolation.yaml"
    overlay.write_text(f'''services:
  tts:
    image: {IMAGE}
    container_name: {name}
    ports: !override ["127.0.0.1:0:8880"]
    # Loading two CPU workers on a contended test host exceeds the shipped
    # thirty-second grace. This fixture tests the client, not startup timing.
    healthcheck:
      start_period: 300s
''')
    compose = ["docker", "compose", "--project-name", name, "--project-directory", str(install),
               "-f", str(recipe), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, text=True, capture_output=True, timeout=480)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], text=True, capture_output=True, timeout=30)
            print(logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def cli(*args, stdin=None):
        result = subprocess.run(["bash", str(ROOT / "ods-cli"), "tts", *args],
                                cwd=tmp_path, text=True, input=stdin, capture_output=True, timeout=360,
                                env={**os.environ, "INSTALL_DIR": str(install), "NO_COLOR": "1"})
        assert result.returncode == 0, result.stderr
        return result.stdout

    try:
        plan = json.loads(run(*compose, "config", "--format", "json"))["services"]["tts"]
        assert plan["image"] == IMAGE
        assert plan["environment"]["UVICORN_WORKERS"] == "2"
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        run(*compose, "up", "-d", "--wait", "--wait-timeout", "360")
        port = run(*compose, "port", "tts", "8880").rsplit(":", 1)[1]
        (install / ".env").write_bytes(f"TTS_PORT='{port}' # test listener\r\n".encode())
        voices = json.loads(cli("voices"))["voices"]
        assert "af_heart" in voices
        output = tmp_path / 'local "speech" âm.wav'
        cli("speak", "-", output.name, "--voice", "af_heart", stdin="Hello from your local ODS server.")
        with wave.open(str(output), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
            frames = audio.getnframes()
            samples = array.array("h", audio.readframes(frames))
            assert len(samples) == frames > 24000
            assert max(abs(sample) for sample in samples) > 100
        assert output.stat().st_mode & 0o777 == 0o600
        assert not list(tmp_path.glob(".ods-speech-*"))
    finally:
        run(*compose, "down", "--timeout", "10")
