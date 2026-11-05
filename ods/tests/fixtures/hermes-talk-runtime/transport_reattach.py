import json
import os
import pathlib
import tempfile
import threading
import faulthandler
from unittest.mock import patch

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="ods-ws-reattach-")
os.environ.setdefault("HERMES_TUI_GATEWAY_NO_FLUSH", "1")

import tui_gateway.server as S
from tui_gateway import ws as WS

from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient

faulthandler.dump_traceback_later(45, exit=True)

db = S._get_db()
assert db is not None
KEY = "ods-ws-reattach-key"
OTHER = "ods-ws-reattach-other"
CONC = "ods-ws-concurrent-key"
for k, model in (
    (KEY, "native-ws-model"),
    (OTHER, "other-model"),
    (CONC, "native-ws-model"),
):
    db.create_session(
        k,
        source="web",
        model=model,
        model_config={"model": model, "provider": "custom"},
    )
db.append_message(KEY, "user", "stored user line alpha")
db.append_message(KEY, "assistant", "stored assistant line beta")
db.end_session(KEY, "diagnostic")
db.append_message(OTHER, "user", "unrelated session payload")
db.end_session(OTHER, "diagnostic")
db.append_message(CONC, "user", "concurrent stored line")
db.end_session(CONC, "diagnostic")

home = pathlib.Path(os.environ["HERMES_HOME"])
S.record_turn_start(home, KEY, "Interrupted fixture prompt; must never run")
S.record_turn_start(home, CONC, "Concurrent fixture prompt; must never run")
marker_before = S.read_turn_marker(home, KEY)
scheduled = []


def _refuse_build(*a, **k):
    return None


def _refuse_work(*a, **k):
    scheduled.append("work")
    raise AssertionError("real prompt execution forbidden")


def _refuse_submit(*a, **k):
    scheduled.append("submit")
    raise AssertionError("real prompt submit forbidden")


app = FastAPI()


@app.websocket("/api/ws")
async def _ws(websocket: WebSocket):
    await WS.handle_ws(websocket)


def _recv_until(ws, pred, limit=400):
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if pred(frame):
            return frame
    raise AssertionError("expected frame not received")


def _is_ready(f):
    return (
        f.get("method") == "event"
        and f.get("params", {}).get("type") == "gateway.ready"
    )


def _rpc(ws, rid, method, params):
    ws.send_text(
        json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    )
    return _recv_until(ws, lambda f: f.get("id") == rid)


def _resume(ws, rid, sid):
    return _rpc(
        ws, rid, "session.resume", {"session_id": sid, "ods_talk_manual_resume": True}
    )


def _event_for(ws, sid, etype, limit=400):
    for _ in range(limit):
        f = json.loads(ws.receive_text())
        p = f.get("params") or {}
        if (
            f.get("method") == "event"
            and p.get("type") == etype
            and p.get("session_id") == sid
        ):
            return f
    raise AssertionError(f"event {etype} for {sid} not received")


def _ping_barrier(ws, rid):
    # gateway.ping is a barrier: _emit returns after scheduling delivery, so
    # collect frames until the ping reply to prove no further frames are queued.
    return _rpc(ws, rid, "gateway.ping", {})


def _collect_until_ping(ws, rid):
    frames = []
    while True:
        f = json.loads(ws.receive_text())
        if f.get("id") == rid:
            return frames
        frames.append(f)


with (
    patch.object(S, "resolve_skin", return_value={}),
    patch.object(S, "_ensure_skin_watcher", return_value=None),
    patch.object(S, "_ensure_lease_watcher", return_value=None),
    patch.object(S, "_start_backend_heartbeat_refresher", return_value=None),
    patch.object(S, "_schedule_startup_orphan_sweep", return_value=None),
    patch.object(S, "_schedule_agent_build", side_effect=_refuse_build),
    patch.object(S, "_schedule_session_cap_enforcement", return_value=None),
    patch.object(S, "_start_session_work", side_effect=_refuse_work),
    patch.object(S, "_run_prompt_submit", side_effect=_refuse_submit),
):
    client = TestClient(app)

    # First-turn drafts have no SQLite row yet. They must be resumable on
    # the same authenticated transport with explicit, authoritative idle state.
    with client.websocket_connect("/api/ws") as fresh_ws:
        _recv_until(fresh_ws, _is_ready)
        created = _rpc(fresh_ws, "fresh-create", "session.create", {})
        assert "error" not in created, created
        fresh = created["result"]
        assert fresh["session_id"] != fresh["stored_session_id"]
        resumed = _resume(fresh_ws, "fresh-resume", fresh["stored_session_id"])
        assert "error" not in resumed, resumed
        fresh_result = resumed["result"]
        assert fresh_result["session_id"] == fresh["session_id"]
        assert fresh_result["stored_session_id"] == fresh["stored_session_id"]
        assert fresh_result["messages"] == []
        assert fresh_result["status"] == "idle" and fresh_result["running"] is False
        assert fresh_result["ods_recovery_pending"] is False
        assert not scheduled, scheduled

    with client.websocket_connect("/api/ws") as ws1:
        _recv_until(ws1, _is_ready)
        r1 = _resume(ws1, "r1", KEY)
        assert "error" not in r1, r1
        d1 = r1["result"]
        runtime_id = d1["session_id"]
        assert runtime_id != KEY
        assert (
            d1.get("stored_session_id", d1.get("session_key", d1.get("resumed"))) == KEY
        )
        msgs1 = d1["messages"]
        assert any("stored user line alpha" in json.dumps(m) for m in msgs1)
        assert any("stored assistant line beta" in json.dumps(m) for m in msgs1)
        model1 = d1["info"].get("model")
        assert model1 == "native-ws-model", d1["info"]
        assert not scheduled, scheduled

        with client.websocket_connect("/api/ws") as ws2:
            _recv_until(ws2, _is_ready)
            r2 = _resume(ws2, "r2", KEY)
            assert "error" not in r2, r2
            d2 = r2["result"]
            assert d2["session_id"] == runtime_id
            assert (
                d2.get("stored_session_id", d2.get("session_key", d2.get("resumed")))
                == KEY
            )
            assert d2["messages"] == msgs1
            # With agent construction intercepted, upstream's live display info
            # reports its default. Verify the model override the agent will use;
            # preserve the display discrepancy in the receipt, not as a pass.
            live_display_model = d2["info"].get("model")
            assert S._sessions[runtime_id]["model_override"]["model"] == model1
            assert not scheduled, scheduled

            assert d1["ods_recovery_pending"] is True
            assert d2["ods_recovery_pending"] is True
            assert S.read_turn_marker(home, KEY) == marker_before
            # Emit a real known message.delta event via S._emit (routes by sid).
            ok = S._emit("message.delta", runtime_id, {"text": "fixture-event"})
            assert ok is True
            ev = _event_for(ws2, runtime_id, "message.delta")
            assert ev["params"]["payload"] == {"text": "fixture-event"}
            # Label: this is transport evidence, not a model reply.

            # Barrier: ping and collect frames until reply; assert no prompt replay.
            ws1.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "p1",
                        "method": "gateway.ping",
                        "params": {},
                    }
                )
            )
            ws2.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "p2",
                        "method": "gateway.ping",
                        "params": {},
                    }
                )
            )
            for ws, rid in ((ws1, "p1"), (ws2, "p2")):
                for f in _collect_until_ping(ws, rid):
                    p = f.get("params") or {}
                    assert p.get("type") not in ("prompt.submitted",), f
            assert not scheduled, scheduled

            with client.websocket_connect("/api/ws") as ws3:
                _recv_until(ws3, _is_ready)
                r3 = _resume(ws3, "r3", OTHER)
                assert "error" not in r3, r3
                other_runtime = r3["result"]["session_id"]
                assert other_runtime != runtime_id
                ok = S._emit("message.delta", runtime_id, {"text": "fixture-event"})
                assert ok is True
                ev2 = _event_for(ws2, runtime_id, "message.delta")
                assert ev2["params"]["payload"] == {"text": "fixture-event"}
                ws3.send_text(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "p3",
                            "method": "gateway.ping",
                            "params": {},
                        }
                    )
                )
                leaked = [
                    f
                    for f in _collect_until_ping(ws3, "p3")
                    if (f.get("params") or {}).get("session_id") == runtime_id
                ]
                assert not leaked, leaked

    # Reconnect after first socket actually closed: stored identity/history invariant.
    with client.websocket_connect("/api/ws") as ws4:
        _recv_until(ws4, _is_ready)
        r4 = _resume(ws4, "r4", KEY)
        assert "error" not in r4, r4
        d4 = r4["result"]
        assert (
            d4.get("stored_session_id", d4.get("session_key", d4.get("resumed"))) == KEY
        )
        assert any("stored user line alpha" in json.dumps(m) for m in d4["messages"])
        assert S._sessions[d4["session_id"]]["model_override"]["model"] == model1
        assert not scheduled, scheduled

    # Concurrent cold resume: two clients, start barrier, shared live window.
    results = []
    start_barrier = threading.Barrier(2, timeout=20)
    close_barrier = threading.Barrier(2, timeout=20)

    def _worker(rid):
        with client.websocket_connect("/api/ws") as w:
            _recv_until(w, _is_ready)
            start_barrier.wait()
            r = _resume(w, rid, CONC)
            results.append(r)
            close_barrier.wait()

    t1 = threading.Thread(target=_worker, args=("c1",))
    t2 = threading.Thread(target=_worker, args=("c2",))
    t1.start()
    t2.start()
    t1.join(25)
    t2.join(25)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(results) == 2, results
    for r in results:
        assert "error" not in r, r
    sids = {r["result"]["session_id"] for r in results}
    assert len(sids) == 1, sids
    assert not scheduled, scheduled

assert S.read_turn_marker(home, KEY) == marker_before
faulthandler.cancel_dump_traceback_later()
print(
    json.dumps(
        {
            "passed": True,
            "runtimeId": runtime_id,
            "storedId": KEY,
            "model": model1,
            "liveDisplayModelWithAgentBuildIntercepted": live_display_model,
            "modelBuildIntercepted": True,
            "messagesRestored": len(msgs1),
            "secondSocketReattached": True,
            "sessionScopedEventDelivered": True,
            "unrelatedSessionIsolated": True,
            "noPromptReplay": True,
            "reconnectAfterClose": True,
            "firstTurnUnpersistedResume": True,
            "concurrentColdResumeSingleRuntime": True,
            "scheduled": scheduled,
            "scope": "transport reattachment over real handle_ws + session.resume + SQLite; agent build/prompt execution refused",
        }
    )
)
