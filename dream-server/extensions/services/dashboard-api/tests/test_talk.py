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


# ----------------------------------------------------------------------
# SSE streaming endpoint tests (/api/talk/message/stream)
# ----------------------------------------------------------------------


def _parse_sse_frames(body: bytes):
    """Split an SSE response body into one dict per frame."""
    import json as _json
    frames = []
    for chunk in body.decode("utf-8").split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        data_lines = [line[5:].lstrip() for line in chunk.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        try:
            frames.append(_json.loads("\n".join(data_lines)))
        except _json.JSONDecodeError:
            pass
    return frames


def test_talk_message_stream_emits_session_then_deltas_then_complete(talk_client, monkeypatch):
    async def fake_stream(session_key, text):
        assert text == "hello"
        yield {"type": "session", "session_id": "sid-stream-1"}
        yield {"type": "delta", "text": "Hello"}
        yield {"type": "delta", "text": " world"}
        yield {"type": "complete", "session_id": "sid-stream-1", "text": "Hello world",
               "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hello"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse_frames(resp.content)
    types = [f["type"] for f in frames]
    # Required ordering: session → deltas → complete → done. Bridge errors
    # replace `complete` with `error`, but `done` is always last.
    assert types[0] == "session"
    assert frames[0]["session_id"] == "sid-stream-1"
    delta_texts = [f["text"] for f in frames if f["type"] == "delta"]
    assert delta_texts == ["Hello", " world"]
    assert any(f["type"] == "complete" and f["text"] == "Hello world" for f in frames)
    assert types[-1] == "done"


def test_talk_message_stream_emits_error_frame_and_done_on_bridge_failure(talk_client, monkeypatch):
    import hermes_bridge as bridge

    async def fake_stream(session_key, text):
        # Yield nothing — go straight to raising. The endpoint should still
        # emit an `error` SSE frame followed by `done` so the client knows the
        # stream closed cleanly.
        if False:
            yield  # pragma: no cover — needed to make this an async generator
        raise bridge.HermesBridgeError("upstream tripped")

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200, resp.text
    frames = _parse_sse_frames(resp.content)
    types = [f["type"] for f in frames]
    assert "error" in types
    error_frame = next(f for f in frames if f["type"] == "error")
    assert error_frame["status_code"] == 502
    assert "upstream tripped" in error_frame["detail"]
    assert types[-1] == "done"


def test_talk_message_stream_emits_503_when_hermes_unavailable(talk_client, monkeypatch):
    import hermes_bridge as bridge

    async def fake_stream(session_key, text):
        if False:
            yield  # pragma: no cover
        raise bridge.HermesUnavailable("hermes is offline")

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.content)
    error_frame = next(f for f in frames if f["type"] == "error")
    assert error_frame["status_code"] == 503


def test_talk_message_stream_requires_session(test_client):
    resp = test_client.post(
        "/api/talk/message/stream",
        json={"text": "hi"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 401


def test_talk_message_stream_validates_input(talk_client):
    resp = talk_client.post("/api/talk/message/stream", json={"text": ""})
    assert resp.status_code == 422

    resp = talk_client.post("/api/talk/message/stream", json={"text": "x" * 8001})
    assert resp.status_code == 413


def test_talk_message_stream_sets_unbuffered_headers(talk_client, monkeypatch):
    """nginx upstream needs ``X-Accel-Buffering: no`` + ``Cache-Control: no-cache``
    so each SSE frame is forwarded immediately. Regression guard for the SSE
    path: if either header is dropped, the dashboard nginx proxy will buffer
    the response and the phone will see the full reply only at the end."""
    async def fake_stream(session_key, text):
        yield {"type": "session", "session_id": "sid-h"}
        yield {"type": "complete", "session_id": "sid-h", "text": "ok", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.headers.get("x-accel-buffering") == "no"
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
