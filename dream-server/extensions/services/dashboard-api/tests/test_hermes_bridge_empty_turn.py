"""Tests for hermes_bridge.py empty-turn degradation (issue #1497).

Upstream Hermes may return a message.complete event with no text field —
e.g. on thinking-only turns or tool-terminated output under load. These
tests verify that hermes_bridge degrades gracefully in each case rather
than crashing or silently swallowing the event.
"""

import asyncio

import pytest

import hermes_bridge


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run(coro_factory):
    """Run a coroutine factory on a fresh event loop and cleanly shut down
    the bridge pool afterwards (mirrors the pattern used in test_talk.py)."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro_factory())
        loop.run_until_complete(hermes_bridge.shutdown_pool())
        return result
    finally:
        loop.close()


class _FakeWS:
    closed = False

    async def send_str(self, _data):
        pass

    async def close(self):
        self.closed = True


class _FakeHTTP:
    async def close(self):
        pass


def _make_conn(session_id: str = "sid-test") -> hermes_bridge._HermesConnection:
    return hermes_bridge._HermesConnection(
        http_session=_FakeHTTP(),
        ws=_FakeWS(),
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Test (a): message.complete payload missing text key, no prior deltas
# → complete event emitted with text="" and warning="empty_turn"
# ---------------------------------------------------------------------------

def test_empty_turn_no_text_key_no_deltas(monkeypatch):
    """message.complete arrives with no 'text' key and no prior delta chunks.

    Expected: a 'complete' event is yielded with text="" and warning="empty_turn".
    No exception is raised.
    """
    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    frames_received = []

    # Deliver: first a prompt.submit RPC ack, then message.complete with no text.
    recv_responses = iter([
        # Prompt.submit ack — bridge skips this (no "event" method).
        {"id": None, "result": {"status": "streaming"}},
        # message.complete with no 'text' key in payload.
        {
            "method": "event",
            "params": {
                "type": "message.complete",
                "payload": {"status": "ok"},   # no "text" key at all
            },
        },
    ])

    async def fake_recv(_ws, _timeout):
        return next(recv_responses)

    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    conn = _make_conn()

    async def drive():
        async for ev in hermes_bridge._submit_on_connection(conn, "hi", 30):
            frames_received.append(ev)

    _run(drive)

    assert len(frames_received) == 1
    ev = frames_received[0]
    assert ev["type"] == "complete"
    assert ev["text"] == ""
    assert ev["warning"] == "empty_turn"
    assert ev["status"] == "ok"


# ---------------------------------------------------------------------------
# Test (b): message.complete payload has text=None, but prior deltas exist
# → complete event emitted with text = accumulated deltas, warning=None
# ---------------------------------------------------------------------------

def test_empty_text_none_falls_back_to_deltas(monkeypatch):
    """message.complete arrives with text=None, but deltas were streamed first.

    Expected: complete event uses the concatenated delta text; warning stays None
    (this is a normal turn that happened to set text=None in the final event).
    """
    hermes_bridge._CONNECTION_POOL.clear()
    hermes_bridge._SWEEPER_TASK = None

    frames_received = []

    recv_responses = iter([
        # Two delta events.
        {
            "method": "event",
            "params": {
                "type": "message.delta",
                "payload": {"text": "Hello"},
            },
        },
        {
            "method": "event",
            "params": {
                "type": "message.delta",
                "payload": {"text": " world"},
            },
        },
        # message.complete with text=None — bridge must fall back to chunks.
        {
            "method": "event",
            "params": {
                "type": "message.complete",
                "payload": {"text": None, "status": "ok"},
            },
        },
    ])

    async def fake_recv(_ws, _timeout):
        return next(recv_responses)

    monkeypatch.setattr("hermes_bridge._recv_json", fake_recv)

    conn = _make_conn()

    async def drive():
        async for ev in hermes_bridge._submit_on_connection(conn, "hi", 30):
            frames_received.append(ev)

    _run(drive)

    complete_events = [e for e in frames_received if e["type"] == "complete"]
    assert len(complete_events) == 1
    ev = complete_events[0]
    assert ev["text"] == "Hello world"
    # Prior deltas exist → not an empty turn → warning should be None (or whatever
    # Hermes put in the payload, which is absent here).
    assert ev["warning"] is None


# ---------------------------------------------------------------------------
# Test (c): submit_prompt() with valid session_id but empty text
# → returns HermesReply(text="") without raising
# ---------------------------------------------------------------------------

def test_submit_prompt_empty_text_returns_reply_without_raising(monkeypatch):
    """submit_prompt() receives a complete event that has a valid session_id
    but empty text (thinking-only turn).

    Expected: returns HermesReply(text="") — does NOT raise HermesBridgeError.
    """

    async def fake_stream(_session_key, _text):
        yield {"type": "session", "session_id": "sid-think"}
        yield {
            "type": "complete",
            "session_id": "sid-think",
            "text": "",
            "status": "ok",
            "warning": "empty_turn",
        }

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    async def drive():
        return await hermes_bridge.submit_prompt("key", "ping")

    reply = _run(drive)

    assert isinstance(reply, hermes_bridge.HermesReply)
    assert reply.text == ""
    assert reply.session_id == "sid-think"


# ---------------------------------------------------------------------------
# Test (d): submit_prompt() with no session_id at all → raises HermesBridgeError
# ---------------------------------------------------------------------------

def test_submit_prompt_no_session_id_raises(monkeypatch):
    """submit_prompt() receives a complete event but never gets a session_id
    (the WS handshake never completed).

    Expected: raises HermesBridgeError — this is a real failure, not a
    graceful empty-turn degradation.
    """

    async def fake_stream(_session_key, _text):
        # Yield a complete event but no session frame and no session_id on complete.
        yield {
            "type": "complete",
            "session_id": "",
            "text": "",
            "status": "ok",
            "warning": "empty_turn",
        }

    monkeypatch.setattr("hermes_bridge.stream_prompt", fake_stream)

    async def drive():
        return await hermes_bridge.submit_prompt("key", "ping")

    with pytest.raises(hermes_bridge.HermesBridgeError):
        _run(drive)
