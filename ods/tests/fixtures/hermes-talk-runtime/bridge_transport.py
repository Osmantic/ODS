"""Real bridge/WS transport contract; replies are synthetic, auth is excluded."""

# ruff: noqa: E402
# Isolated HERMES_HOME must be set before importing the native runtime.
import asyncio
import faulthandler
import json
import os
import pathlib
import socket
import tempfile
import time
from unittest.mock import patch

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="ods-native-bridge-")
os.environ.setdefault("HERMES_TUI_GATEWAY_NO_FLUSH", "1")
os.environ["HERMES_INTERNAL_URL"] = "http://127.0.0.1:0"  # replaced below

faulthandler.dump_traceback_later(45, exit=True)

import tui_gateway.server as S
from tui_gateway import ws as WS
import ods_talk_resume

ods_talk_resume.install(S)

import uvicorn
from fastapi import FastAPI, WebSocket

# ---- dashboard-api modules (readonly mount) ----
import sys

sys.path.insert(0, "/opt/ods-dashboard-api")
import hermes_bridge
import talk_conversations as TC
from talk_store import TalkStore

# ---- prebound socket, random port ----
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
sock.listen(128)
port = sock.getsockname()[1]
os.environ["HERMES_INTERNAL_URL"] = f"http://127.0.0.1:{port}"

# ---- fixture prompt interception: replace prompt.submit handler ----
submissions = []
creates = []
resumes = []
connections = []
original_create = S._methods["session.create"]
original_resume = S._methods["session.resume"]


def counted_create(rid, params):
    result = original_create(rid, params)
    creates.append(result)
    return result


def counted_resume(rid, params):
    result = original_resume(rid, params)
    resumes.append(json.loads(json.dumps(result)))
    return result


S._methods["session.create"] = counted_create
S._methods["session.resume"] = counted_resume


def _fixture_prompt(rid, params):
    sid = params.get("session_id", "")
    text = params.get("text", "")
    submissions.append({"session_id": sid, "text": text})
    with S._sessions_lock:
        session = S._sessions.get(sid)
    if session is None:
        return S._err(rid, 4004, "unknown session")
    # Persist user row via real native path (mirrors _persist_session_row_for_submit).
    error = S._persist_session_row_for_submit(rid, session, text, None)
    assert error is None, error
    # Synthetic history is deliberate: no model/agent runs in this fixture.
    answer = "fixture-complete:" + text
    session["history"].extend(
        [
            {"role": "user", "content": text},
            {"role": "assistant", "content": answer},
        ]
    )
    S._get_db().append_message(session["session_key"], "assistant", answer)
    # Emit real known-shape events via S._emit (routes by sid to owning WS).
    S._emit("message.delta", sid, {"text": "fixture-delta:" + text})
    S._emit(
        "message.complete",
        sid,
        {
            "text": "fixture-complete:" + text,
            "status": "complete",
        },
    )
    return S._ok(rid, {"status": "streaming"})


S._methods["prompt.submit"] = _fixture_prompt


# ---- guards: never build/start real agent work ----
def _refuse_build(*a, **k):
    return None


def _refuse_work(*a, **k):
    raise AssertionError("real session work forbidden")


app = FastAPI()


@app.websocket("/api/ws")
async def _ws(websocket: WebSocket):
    await WS.handle_ws(websocket)


# ---- server subclass with startup event ----
ready = asyncio.Event()


class _Server(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        ready.set()


config = uvicorn.Config(
    app, log_level="warning", lifespan="off", ws="websockets", loop="asyncio"
)
server = _Server(config)


# ---- patch TC._connect_ws to bypass production auth, use local ws ----
async def _local_connect_ws(session):
    ws = await session.ws_connect(f"ws://127.0.0.1:{port}/api/ws")
    connections.append(ws)
    return ws


TC._connect_ws = _local_connect_ws

# ---- store ----
store = TalkStore(path=os.path.join(os.environ["HERMES_HOME"], "talk.db"))
bridge = TC.ConversationBridge(store)


async def _run():
    serve_task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)

        owner = "owner-a"
        other = "owner-b"
        expires = int(time.time()) + 3600

        # 1. create-before-first-prompt: allocate + stream (creates native session)
        cid = await bridge.allocate(owner, expires)
        events = []
        async for ev in bridge.stream(owner, cid, "hello-one"):
            events.append(ev)
        assert any(e.get("type") == "session" for e in events), events
        assert any(e.get("type") == "complete" for e in events), events
        assert len(submissions) == 1, submissions
        assert submissions[0]["text"] == "hello-one"
        snap = await asyncio.to_thread(store.get, owner, cid)
        stored_id = snap["stored_id"]
        assert stored_id and snap["state"] == "ready", snap
        assert len(creates) == 1 and len(connections) == 1
        assert resumes[0]["result"]["messages"] == []
        assert events[-1]["text"] == "fixture-complete:hello-one"
        assert any(
            e.get("type") == "delta" and e.get("text") == "fixture-delta:hello-one"
            for e in events
        )

        # 2. close pool transport, reconnect existing conversation without create/replay
        key = TC._pool_key(owner, cid)
        async with hermes_bridge._POOL_GUARD:
            conn = hermes_bridge._CONNECTION_POOL.pop(key, None)
        if conn is not None:
            await conn.aclose()
        events2 = []
        async for ev in bridge.stream(owner, cid, "hello-two"):
            events2.append(ev)
        assert any(e.get("type") == "session" for e in events2), events2
        assert any(e.get("type") == "complete" for e in events2), events2
        assert len(submissions) == 2, submissions
        assert submissions[1]["text"] == "hello-two"
        snap2 = await asyncio.to_thread(store.get, owner, cid)
        assert snap2["stored_id"] == stored_id and snap2["state"] == "ready"
        assert len(creates) == 1 and len(connections) == 2
        assert connections[0] is not connections[1] and connections[0].closed
        assert "fixture-complete:hello-one" in json.dumps(
            resumes[1]["result"]["messages"]
        )
        assert events2[-1]["text"] == "fixture-complete:hello-two"

        # 3. wrong-owner refusal: other owner cannot access cid
        try:
            async for _ in bridge.stream(other, cid, "nope"):
                pass
            raise AssertionError("wrong-owner stream should refuse")
        except TC.ConversationError as exc:
            assert exc.code == "not_found", exc.code
        assert len(submissions) == 2, submissions

        # 4. interrupted marker: manual resume refusal with zero additional submissions
        home = pathlib.Path(os.environ["HERMES_HOME"])
        S.record_turn_start(home, stored_id, "interrupted fixture prompt")
        marker = S.read_turn_marker(home, stored_id)
        before = len(submissions)
        try:
            async for _ in bridge.stream(owner, cid, "should-refuse"):
                pass
            raise AssertionError("interrupted marker should refuse")
        except TC.ConversationError as exc:
            assert exc.code == "recovery", exc.code
        assert len(submissions) == before, submissions
        assert S.read_turn_marker(home, stored_id) == marker

        return {
            "passed": True,
            "creates": len(creates),
            "webSockets": len(connections),
            "auth": "excluded: local test connector",
            "modelAcceptance": False,
            "storedId": stored_id,
            "submissions": [s["text"] for s in submissions],
            "reconnectReusedStoredId": True,
            "wrongOwnerRefused": True,
            "interruptedRefusedZeroSubmits": True,
            "scope": "real WS.handle_ws + S.dispatch + session.create/resume + real ConversationBridge; prompt.submit intercepted with fixture handler emitting real S._emit message.delta/complete; synthetic deltas labeled transport fixture, not model acceptance",
        }
    finally:
        try:
            await asyncio.wait_for(hermes_bridge.shutdown_pool(), timeout=5)
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serve_task, timeout=10)
            finally:
                sock.close()


with (
    patch.object(S, "resolve_skin", return_value={}),
    patch.object(S, "_ensure_skin_watcher", return_value=None),
    patch.object(S, "_ensure_lease_watcher", return_value=None),
    patch.object(S, "_start_backend_heartbeat_refresher", return_value=None),
    patch.object(S, "_schedule_startup_orphan_sweep", return_value=None),
    patch.object(S, "_schedule_agent_build", side_effect=_refuse_build),
    patch.object(S, "_schedule_session_cap_enforcement", return_value=None),
    patch.object(S, "_start_session_work", side_effect=_refuse_work),
    patch.object(S, "_run_prompt_submit", side_effect=_refuse_work),
):
    result = asyncio.run(_run())

faulthandler.cancel_dump_traceback_later()
print(json.dumps(result))
