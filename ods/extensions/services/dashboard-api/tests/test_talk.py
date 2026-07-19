"""Tests for the ODS Talk mobile portal API."""

import pytest


@pytest.fixture()
def signed_talk_cookie(monkeypatch):
    import session_signer

    monkeypatch.setenv("ODS_SESSION_SECRET", "test-secret-for-talk")
    session_signer._set_secret_for_tests("test-secret-for-talk")
    return session_signer.issue(ttl_seconds=3600)


@pytest.fixture()
def talk_client(test_client, signed_talk_cookie):
    test_client.cookies.set("ods-session", signed_talk_cookie)
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


def test_talk_speak_streams_audio(talk_client, monkeypatch):
    """The /api/talk/speak endpoint streams MP3 bytes from Kokoro as they
    arrive (instead of buffering the whole reply into a single Response).
    Verify the streamed bytes are concatenated correctly + the right
    headers are set so reverse proxies don't re-buffer the stream."""
    async def fake_stream(text):
        assert text == "read this"
        # Simulate Kokoro emitting two chunks as the audio synthesises.
        yield b"mp3 chunk 1 "
        yield b"mp3 chunk 2"

    monkeypatch.setattr("routers.talk._stream_speech", fake_stream)

    resp = talk_client.post("/api/talk/speak", data={"text": "read this"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"mp3 chunk 1 mp3 chunk 2"
    assert resp.headers["content-type"].startswith("audio/mpeg")
    # Critical for streaming through nginx / ods-proxy: without this
    # the reverse proxy would re-buffer the stream and undo our work.
    assert resp.headers.get("X-Accel-Buffering") == "no"


def test_talk_speak_handles_empty_stream(talk_client, monkeypatch):
    """If Kokoro errors before any audio is produced, the streaming
    response should close cleanly with empty body — the SPA's `if (!resp.ok
    || !resp.body)` short-circuit then suppresses playback without
    interrupting the text chat."""
    async def fake_stream(text):
        # Kokoro upstream failure: nothing to yield.
        if False:
            yield b""  # pragma: no cover

    monkeypatch.setattr("routers.talk._stream_speech", fake_stream)

    resp = talk_client.post("/api/talk/speak", data={"text": "silent"})
    assert resp.status_code == 200
    assert resp.content == b""


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


def _run_with_one_loop(coro_factory):
    """Tests that exercise the bridge run several coroutines on the SAME
    event loop so the background sweeper task doesn't get orphaned across
    loop swaps (which would surface as "Task was destroyed but it is
    pending" warnings). Caller passes a *factory* — a 0-arg callable that
    returns a coroutine — because coroutines themselves can't be re-awaited.
    """
    import asyncio as _asyncio
    import hermes_bridge
    loop = _asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro_factory())
        loop.run_until_complete(hermes_bridge.shutdown_pool())
        return result
    finally:
        loop.close()


def test_hermes_bridge_pool_serializes_same_key_parallels_different_keys(monkeypatch):
    """Connection-pool contract:

      * Two concurrent stream_prompt calls with the same session_key must run
        sequentially (the per-connection lock pins the WS to one prompt at a
        time — Hermes can't multiplex two prompt.submit on one session).
      * Two calls with DIFFERENT session_keys are NOT blocked by each other;
        they get separate pooled connections and run in parallel.

    Stubs the WS + recv layer so we don't need a real Hermes. The pool's
    behaviour is what's under test.
    """
    import asyncio as _asyncio
    import hermes_bridge

    # Clear any pool state from prior tests. Synchronous reset since we
    # haven't started a sweeper yet on this loop.
    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    enter_log: list[str] = []
    exit_log: list[str] = []
    create_counts: dict[str, int] = {}

    class FakeWS:
        closed = False
        async def send_str(self, _): pass
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    async def fake_open(session_key):
        create_counts[session_key] = create_counts.get(session_key, 0) + 1
        conn = hermes_bridge._HermesConnection(
            http_session=FakeHTTP(),
            ws=FakeWS(),
            session_id=f"sid-{session_key}",
        )
        return conn

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def fake_recv(_ws, _timeout):
        # Small delay so the test observes ordering, then deliver a
        # message.complete that ends the stream.
        await _asyncio.sleep(0.1)
        return {
            "method": "event",
            "params": {
                "type": "message.complete",
                "session_id": None,  # bridge accepts either matching or null
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
        # Same-key serialization + pool reuse.
        await _asyncio.gather(drive("k1", "A"), drive("k1", "B"))
        same_enter = list(enter_log)
        same_exit = list(exit_log)
        same_creates = dict(create_counts)

        # Reset for the different-key pass; keep same loop alive.
        enter_log.clear()
        exit_log.clear()
        create_counts.clear()
        await hermes_bridge.shutdown_pool()
        hermes_bridge._CONNECTION_POOL.clear()
        hermes_bridge._SWEEPER_TASK = None

        await _asyncio.gather(drive("phoneA", "A"), drive("phoneB", "B"))
        diff_exit = set(exit_log)
        diff_creates = dict(create_counts)
        return same_enter, same_exit, same_creates, diff_exit, diff_creates

    same_enter, same_exit, same_creates, diff_exit, diff_creates = _run_with_one_loop(main)
    assert same_enter == ["A", "B"]
    assert same_exit == ["A", "B"], f"same-key prompts must serialize, got {same_exit}"
    assert same_creates["k1"] == 1, f"expected pool reuse for k1, got {same_creates['k1']} creates"
    assert diff_exit == {"A", "B"}
    assert diff_creates == {"phoneA": 1, "phoneB": 1}


def test_hermes_bridge_slow_open_does_not_block_other_keys(monkeypatch):
    """Opening a connection is network I/O (token fetch + ws connect +
    session.create) that can hang for the full client timeout when Hermes
    is slow or down. That open must not hold the global pool guard:

      * another phone with a warm pooled connection gets it immediately;
      * a second call for the SAME key waits for the in-flight open and
        shares its result instead of opening a duplicate.
    """
    import asyncio as _asyncio
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._OPENING_LOCKS.clear()
    hermes_bridge._SWEEPER_TASK = None

    class FakeWS:
        closed = False
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    async def main():
        open_started = _asyncio.Event()
        release_open = _asyncio.Event()
        open_calls = {"n": 0}

        async def slow_open(session_key):
            open_calls["n"] += 1
            open_started.set()
            await release_open.wait()
            return hermes_bridge._HermesConnection(
                http_session=FakeHTTP(), ws=FakeWS(), session_id=f"sid-{session_key}",
            )

        monkeypatch.setattr("hermes_bridge._open_connection", slow_open)

        warm = hermes_bridge._HermesConnection(
            http_session=FakeHTTP(), ws=FakeWS(), session_id="sid-warm",
        )
        hermes_bridge._CONNECTION_POOL["warm-key"] = warm

        slow_task = _asyncio.create_task(hermes_bridge._get_connection("cold-key"))
        await open_started.wait()

        # While cold-key's open hangs, the warm key must still be served.
        got = await _asyncio.wait_for(hermes_bridge._get_connection("warm-key"), timeout=1.0)
        assert got is warm

        # A concurrent same-key call shares the in-flight open's result.
        second_task = _asyncio.create_task(hermes_bridge._get_connection("cold-key"))
        await _asyncio.sleep(0.05)
        release_open.set()
        first = await _asyncio.wait_for(slow_task, timeout=1.0)
        second = await _asyncio.wait_for(second_task, timeout=1.0)
        assert first is second
        assert open_calls["n"] == 1, f"expected one open for cold-key, got {open_calls['n']}"
        return True

    assert _run_with_one_loop(main)


def test_hermes_bridge_transparent_retry_on_send_reset(monkeypatch):
    """Most insidious dead-WS pattern: ``ws.closed`` reports False, but the
    next ``send_str`` raises ClientConnectionResetError because the upstream
    Hermes closed the socket between checks (e.g. a Hermes restart that
    raced with the freshness check). The bridge must catch that, evict, and
    transparently retry once on a fresh connection — the user shouldn't see
    "load failed" just because Hermes restarted in the background."""
    import aiohttp
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    # aiohttp.ClientConnectionResetError only exists in aiohttp >= 3.10;
    # requirements.txt allows >= 3.9, where send_str on a closing transport
    # raises the builtin ConnectionResetError instead. The bridge catches
    # both, so the test must raise whichever this aiohttp actually has.
    reset_error = getattr(aiohttp, "ClientConnectionResetError", ConnectionResetError)

    class DeadOnSendWS:
        closed = False
        async def send_str(self, _):
            raise reset_error("Cannot write to closing transport")
        async def close(self):
            self.closed = True

    class LiveWS:
        closed = False
        async def send_str(self, _): pass
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    open_calls = 0

    async def fake_open(session_key):
        nonlocal open_calls
        open_calls += 1
        # First connection has a WS that fails on send; second is healthy.
        ws = DeadOnSendWS() if open_calls == 1 else LiveWS()
        return hermes_bridge._HermesConnection(
            http_session=FakeHTTP(), ws=ws, session_id=f"sid-{open_calls}",
        )

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def fake_recv(_ws, _timeout):
        return {"method": "event", "params": {"type": "message.complete", "payload": {"text": "ok"}}}
    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    async def main():
        events = []
        async for ev in hermes_bridge.stream_prompt("retry-key", "hi"):
            events.append(ev["type"])
            if ev["type"] == "complete":
                break
        return events, open_calls

    events, opens = _run_with_one_loop(main)
    # User got a clean stream: session frame + complete frame, no error.
    assert "complete" in events, f"expected transparent retry to deliver complete, got {events}"
    # Two connection opens: the dead one + the retry's fresh one.
    assert opens == 2, f"expected 1 retry (opens == 2), got {opens}"


def test_hermes_bridge_does_not_retry_after_prompt_submit(monkeypatch):
    """A receive-side WS failure happens after prompt.submit was sent, so the
    bridge must not retry the same prompt. Retrying there can duplicate tool
    calls or append a second answer after partial streamed text. Only the
    pre-submit send failure is safe to retry transparently."""
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    class FakeWS:
        closed = False
        async def send_str(self, _): pass
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    open_calls = 0

    async def fake_open(session_key):
        nonlocal open_calls
        open_calls += 1
        return hermes_bridge._HermesConnection(
            http_session=FakeHTTP(), ws=FakeWS(), session_id=f"sid-{open_calls}",
        )

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def fake_recv(_ws, _timeout):
        raise hermes_bridge.HermesUnavailable("closed after submit")

    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    async def main():
        with pytest.raises(hermes_bridge.HermesUnavailable):
            async for _event in hermes_bridge.stream_prompt("no-retry-key", "hi"):
                pass
        return open_calls, "no-retry-key" in hermes_bridge._CONNECTION_POOL

    opens, still_pooled = _run_with_one_loop(main)
    assert opens == 1, f"receive-side failure must not retry submitted prompt, got {opens} opens"
    assert not still_pooled, "failed connection should be evicted before surfacing the error"


def test_hermes_bridge_pool_evicts_and_recreates_on_dead_ws(monkeypatch):
    """If the cached WS has closed (e.g. Hermes container restarted between
    messages), the pool must evict it and open a fresh connection on the
    next ``stream_prompt`` call rather than silently failing forever."""
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    class FakeWS:
        def __init__(self): self.closed = False
        async def send_str(self, _): pass
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    create_count = 0

    async def fake_open(session_key):
        nonlocal create_count
        create_count += 1
        return hermes_bridge._HermesConnection(
            http_session=FakeHTTP(),
            ws=FakeWS(),
            session_id=f"sid-{create_count}",
        )

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def fake_recv(_ws, _timeout):
        return {"method": "event", "params": {"type": "message.complete", "payload": {"text": "ok"}}}
    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    async def main():
        # First call creates connection #1.
        async for ev in hermes_bridge.stream_prompt("p1", "hi"):
            if ev["type"] == "complete":
                break
        cached = hermes_bridge._CONNECTION_POOL["p1"]

        # Simulate Hermes restart: mark the WS as closed.
        cached.ws.closed = True

        # Next call must NOT reuse the dead connection; must open a fresh one.
        async for ev in hermes_bridge.stream_prompt("p1", "hi again"):
            if ev["type"] == "complete":
                break

        return create_count, hermes_bridge._CONNECTION_POOL["p1"].session_id

    creates, sid = _run_with_one_loop(main)
    assert creates == 2, f"expected 2 connection opens (initial + after dead WS), got {creates}"
    assert sid == "sid-2", f"pool should hold the new connection, has {sid}"


def test_hermes_bridge_pool_sweeper_skips_active_connections(monkeypatch):
    """A prompt can run longer than the idle timeout on a slow first turn.
    The sweeper must not close a connection while its per-connection lock is
    held, even if last_used is old."""
    import asyncio as _asyncio
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    monkeypatch.setattr("hermes_bridge._IDLE_EXPIRY_SECONDS", 0.05)
    monkeypatch.setattr("hermes_bridge._IDLE_SWEEP_INTERVAL", 0.02)

    closed_calls: list[str] = []

    class FakeWS:
        def __init__(self): self.closed = False
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    async def fake_open(session_key):
        conn = hermes_bridge._HermesConnection(http_session=FakeHTTP(), ws=FakeWS(), session_id="sid")
        original_close = conn.aclose
        async def _tracked_close():
            closed_calls.append(session_key)
            await original_close()
        conn.aclose = _tracked_close
        return conn

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def main():
        conn = await hermes_bridge._get_connection("active-key")
        await conn.lock.acquire()
        try:
            conn.last_used -= 100
            hermes_bridge._ensure_sweeper_running()
            await _asyncio.sleep(0.15)
            return "active-key" in hermes_bridge._CONNECTION_POOL, list(closed_calls)
        finally:
            conn.lock.release()

    still_in_pool, closed = _run_with_one_loop(main)
    assert still_in_pool, "active connection should not be swept while its lock is held"
    assert closed == [], f"sweeper should not close active connection, got {closed}"


def test_hermes_bridge_pool_sweeper_evicts_idle_connections(monkeypatch):
    """Connections idle longer than _IDLE_EXPIRY_SECONDS must be closed
    by the background sweeper so a fleet of one-time visitors doesn't
    pin Hermes resources forever."""
    import asyncio as _asyncio
    import hermes_bridge

    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    # Tight timings so the test is fast.
    monkeypatch.setattr("hermes_bridge._IDLE_EXPIRY_SECONDS", 0.05)
    monkeypatch.setattr("hermes_bridge._IDLE_SWEEP_INTERVAL", 0.02)

    closed_calls: list[str] = []

    class FakeWS:
        def __init__(self): self.closed = False
        async def close(self): self.closed = True

    class FakeHTTP:
        async def close(self): pass

    async def fake_open(session_key):
        ws = FakeWS()
        conn = hermes_bridge._HermesConnection(http_session=FakeHTTP(), ws=ws, session_id="sid")
        original_close = conn.aclose
        async def _tracked_close():
            closed_calls.append(session_key)
            await original_close()
        conn.aclose = _tracked_close
        return conn

    monkeypatch.setattr("hermes_bridge._open_connection", fake_open)

    async def main():
        # Plant a connection manually (skip stream_prompt, simpler).
        conn = await hermes_bridge._get_connection("idle-key")
        assert "idle-key" in hermes_bridge._CONNECTION_POOL
        # Backdate last_used so it's clearly past expiry.
        conn.last_used -= 100
        hermes_bridge._ensure_sweeper_running()
        # Wait long enough for the sweep loop to fire at least once.
        await _asyncio.sleep(0.15)
        return "idle-key" in hermes_bridge._CONNECTION_POOL, list(closed_calls)

    still_in_pool, closed = _run_with_one_loop(main)
    assert not still_in_pool, "sweeper should have evicted the idle connection"
    assert "idle-key" in closed, f"sweeper should have called aclose(), got {closed}"


def test_talk_message_stream_forwards_hermes_status_updates(talk_client, monkeypatch):
    """Hermes (pinned image) emits status.update events with pre-formatted
    human-readable text and a `kind` category, e.g.
    ``{"kind": "lifecycle", "text": "⏳ Retrying in 5.1s (attempt 2/3)..."}``.
    The bridge forwards them as ``status`` events and the SSE layer relays
    them verbatim — upstream owns the wording, we just route it to the SPA.
    Regression guard for the user-visible "spinner caption is dynamic" UX."""
    async def fake_stream(session_key, text):
        yield {"type": "session", "session_id": "sid"}
        yield {"type": "status", "label": "⏳ Retrying in 5.1s (attempt 2/3)...", "kind": "lifecycle"}
        yield {"type": "status", "label": "🔎 Searching the web…", "kind": "tool"}
        yield {"type": "delta", "text": "OK"}
        yield {"type": "complete", "session_id": "sid", "text": "OK", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.content)
    status_frames = [f for f in frames if f["type"] == "status"]
    # Both status updates make it through, verbatim text + kind preserved.
    assert len(status_frames) == 2
    assert status_frames[0]["label"] == "⏳ Retrying in 5.1s (attempt 2/3)..."
    assert status_frames[0]["kind"] == "lifecycle"
    assert status_frames[1]["label"] == "🔎 Searching the web…"
    assert status_frames[1]["kind"] == "tool"


def test_talk_message_stream_translates_tool_events_to_status_frames(talk_client, monkeypatch):
    """Hermes emits tool.start / tool.complete events while the agent is
    working. The bridge surfaces those as ``tool_start`` / ``tool_complete``
    events, and the SSE layer translates them into friendly ``status``
    frames the SPA renders as the spinner caption. This is the user-facing
    "what's the agent doing?" visibility added per the operator's feedback —
    a phone shouldn't sit on a plain "Thinking" spinner for 30s while
    web_search is mid-flight."""
    async def fake_stream(session_key, text):
        yield {"type": "session", "session_id": "sid"}
        yield {"type": "tool_start", "tool": "web_search", "detail": "weather in San Francisco"}
        yield {"type": "tool_complete", "tool": "web_search", "duration_s": 1.2, "summary": "Did 1 search"}
        yield {"type": "delta", "text": "Sunny, 64°F."}
        yield {"type": "complete", "session_id": "sid", "text": "Sunny, 64°F.", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "what is the weather?"})
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.content)
    types = [f["type"] for f in frames]
    # Required ordering: session → status(active) → status(cleared) → delta → complete → done
    assert types[0] == "session"
    status_frames = [f for f in frames if f["type"] == "status"]
    assert len(status_frames) == 2, f"expected one status-active + one status-clear, got {status_frames}"
    # First status: friendly label + tool name + detail string preserved.
    assert status_frames[0]["label"] == "Searching the web…"
    assert status_frames[0]["tool"] == "web_search"
    assert status_frames[0]["detail"] == "weather in San Francisco"
    # Second status: label=None signals SPA to drop the caption (default
    # "Thinking…" until next status or delta).
    assert status_frames[1]["label"] is None
    assert status_frames[1]["tool"] is None
    # And the actual content + complete frame still come through unchanged.
    assert any(f["type"] == "delta" and f["text"] == "Sunny, 64°F." for f in frames)
    assert any(f["type"] == "complete" for f in frames)


def test_talk_message_stream_status_label_for_unknown_tool(talk_client, monkeypatch):
    """Unknown tools should still produce a status frame — falling back to
    a literal "Using `<name>`…" caption so the operator can map the tool in
    a future _TOOL_LABELS pass instead of staring at a blank spinner."""
    async def fake_stream(session_key, text):
        yield {"type": "session", "session_id": "sid"}
        yield {"type": "tool_start", "tool": "some_new_skill_2026", "detail": None}
        yield {"type": "complete", "session_id": "sid", "text": "ok", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post("/api/talk/message/stream", json={"text": "hi"})
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.content)
    status_frames = [f for f in frames if f["type"] == "status"]
    assert len(status_frames) == 1
    # Honest fallback — literal name appears in the caption so an operator
    # who sees "Using `foo`…" on the phone knows exactly what to map.
    assert "some_new_skill_2026" in status_frames[0]["label"]


def test_talk_message_stream_status_label_handles_browser_prefix(monkeypatch):
    """Tools we don't enumerate explicitly but share a known prefix get a
    sensible group caption (browser_* → "Browsing…")."""
    from routers.talk import _label_for_tool
    assert _label_for_tool("browser_navigate") == "Browsing…"
    assert _label_for_tool("browser_console") == "Browsing…"
    assert _label_for_tool("memory_save") == "Checking memory…"
    assert _label_for_tool("skill_view") == "Loading a skill…"
    # Honest fallback for genuinely unknown:
    assert _label_for_tool("random_unknown_tool") == "Using `random_unknown_tool`…"


def test_talk_attachment_text_file_routes_through_hermes_with_inlined_content(talk_client, monkeypatch):
    """Text-like attachments (.txt/.md/.json/code/etc.) get their content
    decoded and prepended to the user's caption, then routed through the
    existing Hermes bridge — so the agent retains tools, memory, and the
    same SSE event shape as a regular text message."""
    captured_prompt = None

    async def fake_stream(session_key, text):
        nonlocal captured_prompt
        captured_prompt = text
        yield {"type": "session", "session_id": "sid"}
        yield {"type": "complete", "session_id": "sid", "text": "Reviewed.", "status": "ok", "warning": None}

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    resp = talk_client.post(
        "/api/talk/attachment",
        files={"file": ("notes.md", b"# Hello\nFoo bar", "text/markdown")},
        data={"text": "what's in this?"},
    )
    assert resp.status_code == 200
    assert "Hello" in captured_prompt
    assert "Foo bar" in captured_prompt
    assert "what's in this?" in captured_prompt
    assert "notes.md" in captured_prompt


def test_talk_attachment_unknown_filetype_returns_415(talk_client):
    """Unsupported file types (zip/exe/etc.) should be rejected at the
    attachment surface, not silently passed through. v1 is image-and-text-
    only by design."""
    resp = talk_client.post(
        "/api/talk/attachment",
        files={"file": ("archive.zip", b"PK\x03\x04...", "application/zip")},
        data={"text": ""},
    )
    assert resp.status_code == 415


def test_talk_attachment_image_routes_to_vision_endpoint_bypassing_hermes(talk_client, monkeypatch):
    """Images go to litellm directly (not through Hermes) because Hermes's
    prompt.submit accepts only text — multimodal content arrays are a
    litellm/llama-server-level concept. Regression guard that we don't
    accidentally re-route images through the text-only Hermes path."""
    hermes_stream_called = False

    async def fake_hermes_stream(session_key, text):
        nonlocal hermes_stream_called
        hermes_stream_called = True
        yield {"type": "session", "session_id": "sid"}
        yield {"type": "complete", "session_id": "sid", "text": "wrong-path", "status": "ok", "warning": None}

    async def fake_vision_stream(image_bytes, content_type, prompt_text):
        from routers.talk import _sse_event
        yield _sse_event("session", {"session_id": "vision"})
        yield _sse_event("delta", {"text": "Red."})
        yield _sse_event("complete", {"session_id": "vision", "text": "Red.", "status": "ok", "warning": None})
        yield _sse_event("done", {})

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_hermes_stream)
    monkeypatch.setattr("routers.talk._stream_vision_chat", fake_vision_stream)

    # Minimal valid PNG bytes (PNG signature + IHDR).
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    resp = talk_client.post(
        "/api/talk/attachment",
        files={"file": ("photo.png", png_bytes, "image/png")},
        data={"text": "what color?"},
    )
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.content)
    assert any(f.get("type") == "complete" and f.get("text") == "Red." for f in frames)
    # Hermes was NOT called for an image — image flow bypasses the text
    # agent loop.
    assert not hermes_stream_called


def test_talk_attachment_image_uses_filename_when_mobile_uploads_octet_stream(talk_client, monkeypatch):
    """Mobile browsers sometimes send captured images as octet-stream.
    Filename fallback keeps phone uploads on the image path and gives the
    vision model a real image MIME in the data URL."""
    captured_content_type = None

    async def fake_vision_stream(image_bytes, content_type, prompt_text):
        nonlocal captured_content_type
        from routers.talk import _sse_event
        captured_content_type = content_type
        yield _sse_event("session", {"session_id": "vision"})
        yield _sse_event("complete", {"session_id": "vision", "text": "Red.", "status": "ok", "warning": None})
        yield _sse_event("done", {})

    monkeypatch.setattr("routers.talk._stream_vision_chat", fake_vision_stream)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    resp = talk_client.post(
        "/api/talk/attachment",
        files={"file": ("phone-capture.png", png_bytes, "application/octet-stream")},
        data={"text": "what color?"},
    )
    assert resp.status_code == 200
    assert captured_content_type == "image/png"
    frames = _parse_sse_frames(resp.content)
    assert any(f.get("type") == "complete" and f.get("text") == "Red." for f in frames)


def test_talk_attachment_image_too_large_returns_413(talk_client):
    """Image size cap is enforced at the multipart boundary."""
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (11 * 1024 * 1024)
    resp = talk_client.post(
        "/api/talk/attachment",
        files={"file": ("huge.png", big, "image/png")},
    )
    assert resp.status_code == 413


def test_talk_vision_url_defaults_to_openai_v1(monkeypatch):
    """A root inference URL should be normalized to an OpenAI-compatible base."""
    from routers.talk import _vision_chat_completions_url

    monkeypatch.delenv("ODS_TALK_VISION_URL", raising=False)
    monkeypatch.setenv("LLM_API_URL", "http://llama-server:8080")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/v1")

    assert _vision_chat_completions_url() == "http://llama-server:8080/v1/chat/completions"


def test_talk_vision_url_does_not_duplicate_v1(monkeypatch):
    """Operators may pass a full OpenAI base URL; do not append /v1 twice."""
    from routers.talk import _vision_chat_completions_url

    monkeypatch.setenv("ODS_TALK_VISION_URL", "http://vision.local:8080/v1")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/v1")

    assert _vision_chat_completions_url() == "http://vision.local:8080/v1/chat/completions"


def test_talk_vision_url_preserves_lemonade_api_v1(monkeypatch):
    """Lemonade deployments can use /api/v1 as their OpenAI-compatible base."""
    from routers.talk import _vision_chat_completions_url

    monkeypatch.setenv("ODS_TALK_VISION_URL", "http://host.docker.internal:8080/api/v1")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/v1")

    assert _vision_chat_completions_url() == "http://host.docker.internal:8080/api/v1/chat/completions"


def test_talk_attachment_requires_session(test_client):
    """No cookie ⇒ 401 even with a valid file."""
    resp = test_client.post(
        "/api/talk/attachment",
        files={"file": ("notes.md", b"hi", "text/markdown")},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 401


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


def test_ods_talk_hermes_timeout_is_env_configurable(monkeypatch):
    """Lemonade-backed full models can take longer than the generic Talk
    default before producing the first useful event. The compose overlays
    set this env var for those modes; the bridge must honor it."""
    import hermes_bridge

    monkeypatch.setenv("ODS_TALK_HERMES_TIMEOUT", "900")
    assert hermes_bridge._request_timeout() == 900

    monkeypatch.setenv("ODS_TALK_HERMES_TIMEOUT", "5")
    assert hermes_bridge._request_timeout() == 10
