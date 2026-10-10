"""ODS Talk must use the speech models configured in the install's .env.

docker-compose.base.yml passes AUDIO_STT_MODEL / AUDIO_TTS_* to Open WebUI but
not to dashboard-api, so reading them from the process environment always fell
back to the hardcoded defaults. On CUDA installs the installer selects and
pre-downloads deepdml/faster-whisper-large-v3-turbo-ct2, while Talk kept asking
Speaches for Systran/faster-whisper-base.
"""
import asyncio

import httpx
import pytest

import config
from routers import talk

SPEECH_KEYS = ("AUDIO_STT_MODEL", "AUDIO_TTS_MODEL", "AUDIO_TTS_VOICE", "TTS_VOICE")


@pytest.fixture
def install_env(monkeypatch, tmp_path):
    for key in SPEECH_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))

    def write(text):
        (tmp_path / ".env").write_text(text, encoding="utf-8")

    return write


def test_speech_models_come_from_install_env(install_env):
    install_env(
        "AUDIO_STT_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2\n"
        "AUDIO_TTS_MODEL=kokoro-v2\n"
        "AUDIO_TTS_VOICE=bf_emma\n"
    )

    assert talk._stt_model() == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert talk._tts_model() == "kokoro-v2"
    assert talk._tts_voice() == "bf_emma"


def test_speech_models_keep_defaults_when_unset(install_env):
    install_env("LLM_MODEL=qwen\n")

    assert talk._stt_model() == "Systran/faster-whisper-base"
    assert talk._tts_model() == "kokoro"
    assert talk._tts_voice() == "af_heart"


def test_transcription_requests_the_configured_model(install_env, monkeypatch):
    install_env("AUDIO_STT_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2\n")
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hello"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        talk.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    text = asyncio.run(talk._transcribe_bytes(b"audio", "voice.webm", "audio/webm"))

    assert text == "hello"
    assert b"deepdml/faster-whisper-large-v3-turbo-ct2" in seen["body"]
