"""A failed prompt's late events must not become the next prompt's answer."""

import asyncio
import json
from unittest.mock import AsyncMock

import hermes_bridge as bridge
import pytest
import session_signer


@pytest.fixture
def transport(monkeypatch):
    opened = []
    monkeypatch.setattr(bridge, "_CONNECTION_POOL", {})
    monkeypatch.setattr(bridge, "_OPENING_LOCKS", {})
    monkeypatch.setattr(bridge, "_ensure_sweeper_running", lambda: None)

    async def open_connection(_key):
        ws = AsyncMock()
        ws.closed = False
        ws.sent = []
        ws.send_str.side_effect = lambda raw: ws.sent.append(json.loads(raw))
        conn = bridge._HermesConnection(AsyncMock(), ws, f"session-{len(opened)}")
        opened.append(conn)
        return conn

    monkeypatch.setattr(bridge, "_open_connection", open_connection)
    return opened


@pytest.mark.parametrize("failure", [TimeoutError, bridge.HermesBridgeError])
def test_next_http_prompt_cannot_consume_late_answer(test_client, monkeypatch, transport, failure):
    session_signer._set_secret_for_tests("interrupted-pool-fixture")
    test_client.cookies.set("ods-session", session_signer.issue(ttl_seconds=3600))
    monkeypatch.setattr("routers.talk.get_loaded_model", AsyncMock(return_value=None))
    first = True

    async def recv(ws, _timeout):
        nonlocal first
        if first:
            first = False
            raise failure("interrupted fixture")
        conn = next(item for item in transport if item.ws is ws)
        return {"method": "event", "params": {"session_id": conn.session_id,
                "type": "message.complete", "payload": {
                    "text": "late old answer" if conn is transport[0] else "new answer"}}}

    monkeypatch.setattr(bridge, "_recv_json", recv)
    first_response = test_client.post("/api/talk/message", json={"text": "first question"})
    assert first_response.status_code == 502
    second_response = test_client.post("/api/talk/message", json={"text": "second question"})
    assert second_response.status_code == 200
    assert second_response.json()["text"] == "new answer"
    assert len(transport) == 2
    assert [item.ws.sent[0]["params"]["text"] for item in transport] == ["first question", "second question"]
    transport[0].ws.close.assert_awaited_once()
    transport[0].http_session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["cancel", "close"])
async def test_abandoned_stream_releases_pool_and_transport(monkeypatch, transport, exit_kind):
    started = asyncio.Event()

    async def recv(_ws, _timeout):
        started.set()
        if exit_kind == "close":
            return {"method": "event", "params": {"type": "message.delta", "payload": {"text": "partial"}}}
        await asyncio.Event().wait()

    monkeypatch.setattr(bridge, "_recv_json", recv)
    stream = bridge.stream_prompt("owner", "question")
    assert (await anext(stream))["type"] == "session"
    if exit_kind == "cancel":
        pending = asyncio.create_task(anext(stream))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        assert (await anext(stream))["type"] == "delta"
        await stream.aclose()
    assert "owner" not in bridge._CONNECTION_POOL
    transport[0].ws.close.assert_awaited_once()
    transport[0].http_session.close.assert_awaited_once()
    assert not transport[0].lock.locked()
