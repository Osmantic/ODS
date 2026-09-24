"""Reject unusable TTS payloads before they enter the synthesis executor."""

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient

import server


@pytest.fixture
def synthesis_calls(monkeypatch):
    calls = []

    def generate(text, voice, output_format="WAV"):
        calls.append((text, voice, output_format))
        return {"audio_base64": "YXVkaW8=", "sample_rate": 24000, "format": output_format.lower()}

    def generate_stream(text, voice):
        generate(text, voice)
        return Response(b"audio", media_type="audio/wav")

    monkeypatch.setattr(server, "_generate_audio_sync", generate)
    monkeypatch.setattr(server, "_generate_audio_stream_sync", generate_stream)
    return calls


@pytest.mark.parametrize("path", ["/tts", "/tts/stream"])
@pytest.mark.parametrize("payload", [{"text": ""}, {"text": " \t\n"}, {"text": "hello", "output_format": None}])
def test_invalid_payload_never_schedules_audio(path, payload, synthesis_calls):
    response = TestClient(server.app, raise_server_exceptions=False).post(path, json=payload)
    assert response.status_code == 422
    assert synthesis_calls == []


@pytest.mark.parametrize("path", ["/tts", "/tts/stream"])
def test_default_format_and_optional_voice_still_work(path, synthesis_calls):
    response = TestClient(server.app).post(path, json={"text": " Hello ", "voice_preset": None})
    assert response.status_code == 200
    assert len(synthesis_calls) == 1
    assert synthesis_calls[0][:2] == (" Hello ", None)
    assert synthesis_calls[0][2].upper() == "WAV"
