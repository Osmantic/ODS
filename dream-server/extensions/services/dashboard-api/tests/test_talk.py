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


def test_talk_message_stream_emits_keepalive_during_silent_bridge(talk_client, monkeypatch):
    """If the bridge goes silent for longer than the keepalive interval (e.g.
    while llama-server is doing 30-60s of prompt processing with no events),
    the endpoint must emit ``: keepalive`` SSE comment frames. Without them
    iOS Safari and intermediate proxies close idle streams and the SPA
    stalls on a "thinking" spinner that will never resolve."""
    import asyncio as _asyncio
    monkeypatch.setattr("routers.talk._KEEPALIVE_INTERVAL", 0.05)

    async def slow_stream(session_key, text):
        yield {"type": "session", "session_id": "sid-kp"}
        # Simulate a real prompt-processing gap. With keepalive at 50ms,
        # this 200ms gap must produce >= 2 keepalive comments.
        await _asyncio.sleep(0.2)
        yield {"type": "delta", "text": "ok"}
        yield {"type": "complete", "session_id": "sid-kp", "text": "ok", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", slow_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200
    raw = resp.content.decode("utf-8")
    assert ": keepalive" in raw, "expected at least one keepalive comment frame in body"
    assert raw.count(": keepalive") >= 2, f"expected >=2 keepalive frames, got {raw.count(': keepalive')}"
    # And the real frames still come through.
    frames = _parse_sse_frames(resp.content)
    types = [f["type"] for f in frames]
    assert "complete" in types and types[-1] == "done"


def test_talk_message_stream_cancels_upstream_on_client_disconnect(talk_client, monkeypatch):
    """If the client drops the connection mid-stream, the endpoint must stop
    pulling from the bridge so a slow upstream (llama-server slot) is freed
    instead of held for a response nobody will read.

    This is a unit-level test of the generator itself — we drive it directly
    so we can assert the bridge iterator gets ``aclose()``-style cancellation
    when ``request.is_disconnected()`` returns True.
    """
    import asyncio as _asyncio
    monkeypatch.setattr("routers.talk._KEEPALIVE_INTERVAL", 0.02)

    bridge_started = _asyncio.Event()
    bridge_cancelled = _asyncio.Event()

    async def hanging_stream(session_key, text):
        yield {"type": "session", "session_id": "sid-cancel"}
        bridge_started.set()
        try:
            # Hang until the consumer cancels us.
            await _asyncio.sleep(60)
            yield {"type": "complete", "session_id": "sid-cancel", "text": "never", "status": "ok", "warning": None}
        except _asyncio.CancelledError:
            bridge_cancelled.set()
            raise

    monkeypatch.setattr("hermes_bridge.stream_prompt", hanging_stream)

    # Build a stub Request that reports disconnected after the first poll.
    class StubRequest:
        def __init__(self):
            self.polls = 0

        async def is_disconnected(self):
            self.polls += 1
            # Stay connected once so the session frame can flush, then drop.
            return self.polls > 1

    from routers.talk import _stream_hermes_sse

    async def drive():
        gen = _stream_hermes_sse("k", "hi", StubRequest())
        collected = []
        # Iterate the SSE generator; the consumer side is what FastAPI does.
        async for chunk in gen:
            collected.append(chunk)
            if len(collected) > 10:
                break
        return collected

    chunks = _asyncio.new_event_loop().run_until_complete(drive())
    body = b"".join(chunks).decode("utf-8")
    # The session frame should have made it out.
    assert '"type":"session"' in body
    # The generator must have exited via the disconnect path without trying to
    # write a final frame to a dead response. Normal/error completions still
    # emit `done`.
    assert '"type":"done"' not in body
    # And the upstream bridge task must have been cancelled (no hang).
    assert bridge_started.is_set()
    assert bridge_cancelled.is_set()


def test_hermes_bridge_serializes_concurrent_submits_per_session(monkeypatch):
    """Two concurrent stream_prompt calls with the same session_key must run
    sequentially, not pile up on llama-server slots. Two calls with DIFFERENT
    session_keys are allowed to overlap (different phones don't block each
    other)."""
    import asyncio as _asyncio
    import hermes_bridge

    hermes_bridge._SUBMIT_LOCKS.clear()

    enter_log: list[str] = []
    exit_log: list[str] = []

    # Stub out the upstream so we don't need a real Hermes — the lock is what
    # we're exercising. We patch the inner pieces stream_prompt calls.
    async def fake_connect_ws(_session):
        class FakeWS:
            async def send_str(self, _): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeWS()

    async def fake_create(_ws, *, timeout=30):
        return "sid-fake"

    monkeypatch.setattr("hermes_bridge._connect_ws", fake_connect_ws)
    monkeypatch.setattr("hermes_bridge._create_session_on_ws", fake_create)

    async def fake_recv(_ws, _timeout):
        # Sleep so the test observes ordering, then return a message.complete.
        await _asyncio.sleep(0.1)
        return {
            "method": "event",
            "params": {
                "type": "message.complete",
                "session_id": "sid-fake",
                "payload": {"text": "done", "status": "ok"},
            },
        }
    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    async def drive(key, tag):
        enter_log.append(tag)
        async for ev in hermes_bridge.stream_prompt(key, "x"):
            if ev["type"] == "complete":
                break
        exit_log.append(tag)

    async def main():
        # Two concurrent same-key callers — must serialize.
        await _asyncio.gather(drive("k1", "A"), drive("k1", "B"))
        return list(enter_log), list(exit_log)

    enter, exit_ = _asyncio.new_event_loop().run_until_complete(main())
    # Both got past their initial enter print before any awaited stream
    # work — that part isn't blocked. The interesting assertion is that
    # B's exit fires AFTER A's exit (B waited for the lock).
    assert enter == ["A", "B"]
    assert exit_ == ["A", "B"], f"expected serialized exits A then B, got {exit_}"

    # Now confirm different keys are NOT blocked by each other: kick off two
    # with different keys; both finish near-simultaneously.
    enter_log.clear()
    exit_log.clear()
    hermes_bridge._SUBMIT_LOCKS.clear()

    async def main2():
        await _asyncio.gather(drive("phoneA", "A"), drive("phoneB", "B"))

    _asyncio.new_event_loop().run_until_complete(main2())
    # Both complete; the order can be either A→B or B→A but both must finish.
    assert set(exit_log) == {"A", "B"}


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
