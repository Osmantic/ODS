import asyncio
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

import aiohttp
import pytest
import pytest_asyncio

import talk_conversations as cb
import hermes_bridge as hb
from talk_store import TalkStore


class FakeWS:
    store: TalkStore
    owner: str
    cid: str

    def __init__(self, mode, resume_update, omit_identity, prompt_sent, requests):
        self.mode = mode
        self.resume_update = resume_update
        self.omit_identity = omit_identity
        self.prompt_sent = prompt_sent
        self.requests = requests
        self.queue: asyncio.Queue[aiohttp.WSMessage] = asyncio.Queue()
        self.closed = False
        self.hang_event = asyncio.Event()

    async def send_str(self, data):
        if self.closed:
            raise ConnectionResetError("closed")
        msg = json.loads(data)
        self.requests.append(msg)
        rid = msg["id"]
        method = msg["method"]
        params = msg.get("params") or {}
        if method == "ods.talk.capabilities":
            if self.mode == "hang_caps":
                await self.hang_event.wait()
                return
            if self.mode == "unsupported_caps":
                result = {"manual_resume": False, "version": "1"}
            else:
                result = {"manual_resume": True, "version": "1"}
            await self._push({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "session.create":
            assert self.store.get(self.owner, self.cid)["state"] == "creating"
            if self.mode == "missing_create_id":
                await self._push({"jsonrpc": "2.0", "id": rid, "result": {}})
                return
            await self._push(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "session_id": "rt-1",
                        "stored_session_id": "stored-1",
                    },
                }
            )
        elif method == "session.resume":
            assert params == {"session_id": "stored-1", "ods_talk_manual_resume": True}
            if self.mode == "malformed":
                await self._push(
                    {"jsonrpc": "2.0", "id": rid, "result": {"session_id": "rt-1"}}
                )
                return
            result = {
                "session_key": "stored-1",
                "session_id": "rt-1",
                "messages": [],
                "status": "idle",
                "ods_recovery_pending": False,
            }
            if self.omit_identity:
                result.pop("session_key", None)
            result.update(self.resume_update)
            await self._push({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "prompt.submit":
            assert self.store.get(self.owner, self.cid)["state"] == "submitted"
            assert params["session_id"] == "rt-1"
            if self.mode == "send_failure":
                raise ConnectionResetError("boom")
            if self.mode == "eof_afterprompt":
                self.prompt_sent.set()
                self.closed = True
                self.queue.put_nowait(
                    aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, None, None)
                )
                return
            if self.mode == "hold_afterprompt":
                self.prompt_sent.set()
                return
            if self.mode == "approval_afterprompt":
                self.prompt_sent.set()
                await self._push({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {"type": "approval.request", "session_id": "rt-1",
                               "payload": {"command": "echo safe", "description": "Run code"}},
                })
                return
            self.prompt_sent.set()
            await self._push(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "message.delta",
                        "session_id": "rt-1",
                        "payload": {"text": "hi"},
                    },
                }
            )
            if self.mode == "unknown_terminal":
                status = "weird"
            elif self.mode == "error_terminal":
                status = "error"
            elif self.mode == "interrupted_terminal":
                status = "interrupted"
            else:
                status = "ok"
            await self._push(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "message.complete",
                        "session_id": "rt-1",
                        "payload": {"status": status, "text": "hi"},
                    },
                }
            )
        else:
            await self._push({"jsonrpc": "2.0", "id": rid, "error": {"code": -1}})

    async def _push(self, frame):
        await self.queue.put(
            aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(frame), None)
        )

    async def receive(self):
        return await self.queue.get()

    async def close(self):
        self.closed = True


class FakeHTTP:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@dataclass
class Environment:
    store: TalkStore
    bridge: cb.ConversationBridge
    owner: str
    cid: str
    opened: list[tuple[hb._HermesConnection, FakeWS, FakeHTTP]]
    requests: list[dict[str, Any]]
    prompt_sent: asyncio.Event
    interrupted: list[str]
    state: dict[str, Any]
    monkeypatch: pytest.MonkeyPatch


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    store = TalkStore(str(tmp_path / "talk.db"))
    bridge = cb.ConversationBridge(store)
    owner = "owner"
    cid = await bridge.allocate(owner, int(time.time()) + 3600)
    opened = []
    requests: list[dict[str, Any]] = []
    prompt_sent = asyncio.Event()
    interrupted = []

    async def interrupt_with_ack(session_id):
        interrupted.append(session_id)
    state = {
        "mode": "normal",
        "resume_update": {},
        "omit_identity": False,
    }

    async def fake_open(key):
        ws = FakeWS(
            state["mode"],
            state["resume_update"],
            state["omit_identity"],
            prompt_sent,
            requests,
        )
        ws.store, ws.owner, ws.cid = store, owner, cid
        http = FakeHTTP()
        # These test transports implement the exercised aiohttp operations.
        # The separate native fixtures use real ClientSession/WebSocket objects.
        conn = hb._HermesConnection(
            http_session=cast(aiohttp.ClientSession, http),
            ws=cast(aiohttp.ClientWebSocketResponse, ws), session_id="",
        )
        opened.append((conn, ws, http))
        return conn

    monkeypatch.setattr(cb, "_open_talk_connection", fake_open)
    monkeypatch.setattr(cb, "_ensure_sweeper_running", lambda: None)
    # The existing control-channel RPC has separate native/auth tests. Observe
    # its session target here while retaining real pool/interrupt cleanup.
    monkeypatch.setattr(hb, "_interrupt_session_with_ack", interrupt_with_ack)

    e = Environment(store, bridge, owner, cid, opened, requests, prompt_sent,
                    interrupted, state, monkeypatch)
    try:
        yield e
    finally:
        for conn, ws, http in opened:
            ws.closed = True
        await hb.shutdown_pool()


async def _consume(bridge, owner, cid, text):
    events = []

    async def run():
        async for ev in bridge.stream(owner, cid, text):
            events.append(ev)

    await asyncio.wait_for(run(), 2)
    return events


def _methods(requests):
    return [r["method"] for r in requests]


@pytest.mark.asyncio
async def test_first_and_warm_turn_single_create(env):
    ev1 = await _consume(env.bridge, env.owner, env.cid, "hi")
    assert ev1[0]["type"] == "session"
    assert ev1[-1]["type"] == "complete"
    assert _methods(env.requests).count("session.create") == 1
    ev2 = await _consume(env.bridge, env.owner, env.cid, "again")
    assert ev2[0]["type"] == "session"
    assert _methods(env.requests).count("session.create") == 1


@pytest.mark.asyncio
async def test_shutdown_pool_new_bridge_retains_stored(env):
    await _consume(env.bridge, env.owner, env.cid, "hi")
    await hb.shutdown_pool()
    env.opened.clear()
    env.requests.clear()
    bridge2 = cb.ConversationBridge(TalkStore(env.store.path))
    ev = await _consume(bridge2, env.owner, env.cid, "again")
    assert ev[0]["type"] == "session"
    assert "session.create" not in _methods(env.requests)
    assert "session.resume" in _methods(env.requests)


@pytest.mark.asyncio
async def test_wrong_owner_opens_zero(env):
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, "other", env.cid, "hi")
    assert env.opened == []


@pytest.mark.asyncio
async def test_missing_create_then_retry(env):
    env.state["mode"] = "missing_create_id"
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    env.state["mode"] = "normal"
    with pytest.raises(cb.ConversationError, match="unresolved"):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert _methods(env.requests).count("session.create") == 1


@pytest.mark.asyncio
async def test_send_failure_uncertain(env):
    env.state["mode"] = "send_failure"
    with pytest.raises(hb.HermesBridgeError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "uncertain"
    assert _methods(env.requests).count("prompt.submit") == 1


@pytest.mark.asyncio
async def test_eof_after_prompt_uncertain(env):
    env.state["mode"] = "eof_afterprompt"
    with pytest.raises(hb.HermesBridgeError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "uncertain"
    assert _methods(env.requests).count("prompt.submit") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag",
    [
        "running",
        "inflight",
        "queued",
        "open_requests",
        "pending_approval",
        "pending_connection",
        "hydrating",
        "auto_continue",
    ],
)
async def test_busy_flags_reject(env, flag):
    env.state["resume_update"] = {flag: True}
    with pytest.raises(cb.ConversationError) as ei:
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert ei.value.code == "busy"
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_native_interrupted_marker_never_becomes_a_new_prompt(env):
    env.state["resume_update"] = {"ods_recovery_pending": True}
    with pytest.raises(cb.ConversationError) as error:
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert error.value.code == "recovery"
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"session_key": "other"}, {"messages": None}])
async def test_unverified_native_history_is_not_accepted(env, update):
    env.state["resume_update"] = update
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert env.store.get(env.owner, env.cid)["state"] == "ready"
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_malformed_resume_rejects(env):
    env.state["mode"] = "malformed"
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_missing_identity_rejects(env):
    env.state["omit_identity"] = True
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_echoed_resume_target_is_not_stored_identity_proof(env):
    env.state["omit_identity"] = True
    env.state["resume_update"] = {"resumed": "stored-1"}
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert "prompt.submit" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_unsupported_caps_no_create(env):
    env.state["mode"] = "unsupported_caps"
    with pytest.raises(cb.ConversationError) as ei:
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert ei.value.code == "unsupported"
    assert "session.create" not in _methods(env.requests)


@pytest.mark.asyncio
async def test_aclose_before_prompt(env):
    gen = env.bridge.stream(env.owner, env.cid, "hi")
    first = await asyncio.wait_for(gen.__anext__(), 2)
    assert first["type"] == "session"
    await asyncio.wait_for(gen.aclose(), 2)
    assert "prompt.submit" not in _methods(env.requests)
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error_terminal", "interrupted_terminal"])
async def test_terminal_ready(env, mode):
    env.state["mode"] = mode
    events = await _consume(env.bridge, env.owner, env.cid, "hi")
    assert events[-1]["type"] == "complete"
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "ready"


@pytest.mark.asyncio
async def test_unknown_terminal_uncertain(env):
    env.state["mode"] = "unknown_terminal"
    with pytest.raises(cb.ConversationError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "uncertain"


@pytest.mark.asyncio
async def test_hold_cancel_uncertain(env):
    env.state["mode"] = "hold_afterprompt"
    task = asyncio.create_task(_consume(env.bridge, env.owner, env.cid, "hi"))
    await asyncio.wait_for(env.prompt_sent.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]


@pytest.mark.asyncio
async def test_hang_caps_timeout(env):
    env.state["mode"] = "hang_caps"
    env.monkeypatch.setattr(cb, "_RPC_TIMEOUT", 0.03)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_consume(env.bridge, env.owner, env.cid, "hi"), 1)
    assert "session.create" not in _methods(env.requests)
    snap = env.store.get(env.owner, env.cid)
    assert snap["state"] == "new"


def _block_transition(env, method):
    """Hold a second SQLite writer only when the target transition begins.

    The trace callback signals the real transaction's BEGIN IMMEDIATE while
    another connection holds its lock. Earlier reads and claims run normally.
    """
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    local = threading.local()
    blockers = []
    original = getattr(env.store, method)
    original_txn = env.store._txn

    @contextmanager
    def transaction(conn):
        if getattr(local, "target", False):
            def trace(sql):
                if sql == "BEGIN IMMEDIATE":
                    loop.call_soon_threadsafe(entered.set)
            conn.set_trace_callback(trace)
        with original_txn(conn):
            yield

    def wrapped(*args):
        blocker = sqlite3.connect(
            str(env.store.path), isolation_level=None, check_same_thread=False
        )
        blocker.execute("BEGIN IMMEDIATE")
        blockers.append(blocker)
        local.target = True
        try:
            return original(*args)
        finally:
            local.target = False

    def release():
        for blocker in blockers:
            blocker.rollback()

    env.monkeypatch.setattr(env.store, "_txn", transaction)
    env.monkeypatch.setattr(env.store, method, wrapped)
    return entered, release, blockers


async def _turn_without_nested_task(env):
    return [event async for event in env.bridge.stream(env.owner, env.cid, "hi")]


async def _event_loop_barrier():
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,state,prompts,interrupts",
    [
        ("claim_create", "uncertain", 0, []),
        ("finish_create", "ready", 0, []),
        ("claim_turn", "ready", 0, []),
        ("submit_turn", "uncertain", 0, ["rt-1"]),
        ("finish_turn", "ready", 1, []),
    ],
)
async def test_cancel_during_real_sqlite_transition(env, method, state, prompts, interrupts):
    entered, release, blockers = _block_transition(env, method)
    task = asyncio.create_task(_turn_without_nested_task(env))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await _event_loop_barrier()
        # Cancellation cannot abandon a transaction that can still commit.
        assert not task.done()
        release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        for blocker in blockers:
            blocker.close()
    assert env.store.get(env.owner, env.cid)["state"] == state
    assert _methods(env.requests).count("prompt.submit") == prompts
    assert env.interrupted == interrupts


@pytest.mark.asyncio
async def test_repeated_cancel_does_not_abandon_cleanup(env):
    env.state["mode"] = "hold_afterprompt"
    entered, release, blockers = _block_transition(env, "mark_uncertain")
    task = asyncio.create_task(_turn_without_nested_task(env))
    try:
        await asyncio.wait_for(env.prompt_sent.wait(), 2)
        task.cancel()
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(3):
            task.cancel()
            await _event_loop_barrier()
            assert not task.done()
        release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        for blocker in blockers:
            blocker.close()
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]
    assert _methods(env.requests).count("prompt.submit") == 1


@pytest.mark.asyncio
async def test_settle_preserves_cancel_after_inner_failure():
    entered, release = asyncio.Event(), asyncio.Event()

    async def inner():
        entered.set()
        await release.wait()
        raise ValueError("inner failure")

    task = asyncio.create_task(cb._settle(inner()))
    await entered.wait()
    task.cancel()
    await _event_loop_barrier()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_closed_prompt_socket_still_interrupts_owned_native_turn(env):
    env.state["mode"] = "eof_afterprompt"
    with pytest.raises(hb.HermesBridgeError):
        await _consume(env.bridge, env.owner, env.cid, "hi")
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]
    assert _methods(env.requests).count("prompt.submit") == 1


@pytest.mark.asyncio
async def test_completed_turn_preserves_warm_connection(env):
    await _consume(env.bridge, env.owner, env.cid, "hi")
    await _consume(env.bridge, env.owner, env.cid, "again")
    assert len(env.opened) == 1
    assert env.interrupted == []


async def _wire_router(env):
    async def get_bridge():
        return env.bridge
    env.monkeypatch.setattr("talk_runtime.get_bridge", get_bridge)
    env.monkeypatch.setattr("routers.talk._KEEPALIVE_INTERVAL", 0.01)


@pytest.mark.asyncio
async def test_sync_router_closes_completed_bridge_iterator(env):
    from routers.talk import _send_to_hermes
    await _wire_router(env)
    reply = await _send_to_hermes(env.owner, env.cid, "hi")
    assert reply["conversation_id"] == env.cid
    assert reply["text"] == "hi"
    conn = env.opened[0][0]
    assert not conn.lock.locked()
    assert env.store.get(env.owner, env.cid)["state"] == "ready"
    assert env.interrupted == []
    await asyncio.wait_for(_send_to_hermes(env.owner, env.cid, "again"), 2)
    assert _methods(env.requests).count("session.create") == 1
    assert _methods(env.requests).count("prompt.submit") == 2


@pytest.mark.asyncio
async def test_nonstreaming_request_disconnect_interrupts_real_native_turn(env):
    from fastapi import HTTPException
    from routers.talk import _send_to_hermes, _while_connected
    await _wire_router(env)
    env.state["mode"] = "hold_afterprompt"

    class Request:
        async def receive(self):
            await env.prompt_sent.wait()
            return {"type": "http.disconnect"}

    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(_while_connected(Request(), _send_to_hermes(env.owner, env.cid, "hi")), 2)
    assert exc.value.status_code == 499
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]
    assert not env.opened[0][0].lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hold_afterprompt", "approval_afterprompt"])
async def test_http_disconnect_settles_real_bridge_and_pending_approval(env, mode):
    from routers.talk import _TalkStreamingResponse, _stream_hermes_sse
    await _wire_router(env)
    env.state["mode"] = mode
    observed = []
    ready_to_disconnect = asyncio.Event()

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def send(message):
        body = message.get("body", b"")
        observed.append(body)
        # A keepalive after submit proves the model turn is waiting. In the
        # approval case wait until the real parser registered the request.
        if ((mode == "approval_afterprompt" and b'"type":"approval"' in body)
                or (mode == "hold_afterprompt" and env.prompt_sent.is_set()
                    and body == b": keepalive\n\n")):
            ready_to_disconnect.set()

    async def receive():
        await ready_to_disconnect.wait()
        return {"type": "http.disconnect"}

    response = _TalkStreamingResponse(
        _stream_hermes_sse(env.owner, env.cid, "hi", ConnectedRequest()),
        media_type="text/event-stream",
    )
    await asyncio.wait_for(response(
        {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send,
    ), 2)
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]
    assert _methods(env.requests).count("prompt.submit") == 1
    conn, ws, http = env.opened[0]
    assert ws.closed and http.closed and not conn.lock.locked()
    assert b'"type":"done"' not in b"".join(observed)
    if mode == "approval_afterprompt":
        responses = [r for r in env.requests if r["method"] == "approval.respond"]
        assert len(responses) == 1
        assert responses[0]["params"] == {
            "session_id": "rt-1", "choice": "deny", "all": False,
        }


@pytest.mark.asyncio
async def test_repeated_http_cancellation_joins_durable_cleanup(env):
    from routers.talk import _TalkStreamingResponse, _stream_hermes_sse
    await _wire_router(env)
    env.state["mode"] = "hold_afterprompt"
    entered, release, blockers = _block_transition(env, "mark_uncertain")

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def receive():
        await asyncio.Event().wait()

    async def send(_message):
        pass

    response = _TalkStreamingResponse(
        _stream_hermes_sse(env.owner, env.cid, "hi", ConnectedRequest()),
        media_type="text/event-stream",
    )
    task = asyncio.create_task(response(
        {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send,
    ))
    try:
        await asyncio.wait_for(env.prompt_sent.wait(), 2)
        task.cancel()
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(3):
            task.cancel()
            await _event_loop_barrier()
            assert not task.done()
        release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        for blocker in blockers:
            blocker.close()
    assert env.store.get(env.owner, env.cid)["state"] == "uncertain"
    assert env.interrupted == ["rt-1"]
    assert not env.opened[0][0].lock.locked()
