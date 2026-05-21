"""Tests for the Dream Talk mobile portal API."""

import pytest


@pytest.fixture()
def signed_talk_cookie(monkeypatch):
    import session_signer

    monkeypatch.setenv("DREAM_SESSION_SECRET", "test-secret-for-talk")
    session_signer._set_secret_for_tests("test-secret-for-talk")
    return session_signer.issue(ttl_seconds=3600)


@pytest.fixture()
def talk_client(test_client, signed_talk_cookie):
    test_client.cookies.set("dream-session", signed_talk_cookie)
    return test_client


def test_talk_rejects_api_key_without_session(test_client):
    resp = test_client.post(
        "/api/talk/message",
        json={"text": "hello"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 401


def test_talk_status_requires_session(talk_client, monkeypatch):
    async def fake_state(service_id):
        return {"configured": True, "status": "healthy", "id": service_id}

    monkeypatch.setattr("routers.talk._service_state", fake_state)
    resp = talk_client.get("/api/talk/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["capabilities"]["text_chat"] is True
    assert data["capabilities"]["tts"] is True
    assert data["capabilities"]["audio_message"] is True
    assert data["capabilities"]["live_mic_requires_secure_context"] is True


def test_talk_message_routes_through_hermes_bridge(talk_client, monkeypatch):
    from hermes_bridge import HermesReply

    calls = []

    async def fake_submit(session_key, text):
        calls.append((session_key, text))
        return HermesReply(session_id="sid-1", text="hello back")

    monkeypatch.setattr("hermes_bridge.submit_prompt", fake_submit)

    resp = talk_client.post("/api/talk/message", json={"text": "hello"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "hello back"
    assert calls and calls[0][1] == "hello"


def test_talk_audio_message_transcribes_and_routes(talk_client, monkeypatch):
    async def fake_transcribe(data, filename, content_type):
        assert data == b"fake audio"
        assert filename == "voice.webm"
        assert content_type == "audio/webm"
        return "what is running locally"

    async def fake_send(session_key, text):
        return {
            "session_id": "sid-2",
            "text": f"answer to {text}",
            "status": "ok",
            "warning": None,
        }

    monkeypatch.setattr("routers.talk._transcribe_bytes", fake_transcribe)
    monkeypatch.setattr("routers.talk._send_to_hermes", fake_send)

    resp = talk_client.post(
        "/api/talk/audio-message",
        files={"file": ("voice.webm", b"fake audio", "audio/webm")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["transcript"] == "what is running locally"
    assert data["text"] == "answer to what is running locally"


def test_talk_speak_returns_audio(talk_client, monkeypatch):
    async def fake_speak(text):
        assert text == "read this"
        return b"mp3 bytes", "audio/mpeg"

    monkeypatch.setattr("routers.talk._speak_text", fake_speak)

    resp = talk_client.post("/api/talk/speak", data={"text": "read this"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"mp3 bytes"
    assert resp.headers["content-type"].startswith("audio/mpeg")
