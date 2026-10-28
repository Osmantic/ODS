import asyncio
import json
import time

import aiohttp
import pytest
import pytest_asyncio

import talk_conversations as cb
import hermes_bridge as hb
from talk_store import TalkStore


class FakeWS:
    def __init__(self, mode, resume_update, omit_identity, prompt_sent, requests):
        self.mode = mode
        self.resume_update = resume_update
        self.omit_identity = omit_identity
        self.prompt_sent = prompt_sent
        self.requests = requests
        self.queue = asyncio.Queue()
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
                self.queue.put_nowait(
                    aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, None, None)
                )
                return
            if self.mode == "hold_afterprompt":
                self.prompt_sent.set()
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


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    store = TalkStore(str(tmp_path / "talk.db"))
    bridge = cb.ConversationBridge(store)
    owner = "owner"
    cid = await bridge.allocate(owner, int(time.time()) + 3600)
    opened = []
    requests = []
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
        conn = hb._HermesConnection(http_session=http, ws=ws, session_id="")
        opened.append((conn, ws, http))
        return conn

    monkeypatch.setattr(cb, "_open_talk_connection", fake_open)
    monkeypatch.setattr(cb, "_ensure_sweeper_running", lambda: None)
    # The existing control-channel RPC has separate native/auth tests. Observe
    # its session target here while retaining real pool/interrupt cleanup.
    monkeypatch.setattr(hb, "_interrupt_session_with_ack", interrupt_with_ack)

    class Env:
        pass

    e = Env()
    e.store = store
    e.bridge = bridge
    e.owner = owner
    e.cid = cid
    e.opened = opened
    e.requests = requests
    e.prompt_sent = prompt_sent
    e.interrupted = interrupted
    e.state = state
    e.monkeypatch = monkeypatch
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
