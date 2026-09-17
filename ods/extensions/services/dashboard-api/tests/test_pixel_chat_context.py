"""End-to-end dashboard boundary for compaction and complete history delivery."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_pixel import ConnectedRequest, FakeClient, FakeResponse, stream_body
from test_pixel_chat_results import OWNER, store, FINAL
from routers import pixel
from routers import pixel_teams
from pixel_chat_context import HistorySnapshot, MAX_HISTORY_BYTES, public_context


def state():
    return {
        "schemaVersion": 1, "status": "ready", "sessionRevision": "a" * 64,
        "context": {"used": 1200, "window": 8192, "measuredAt": "2026-09-16T12:00:00Z"},
        "model": {"id": "test-model", "provider": "local", "contextWindow": 8192},
        "compaction": {"status": "idle", "count": 0},
        "history": {"status": "ready", "revision": "b" * 64, "acknowledgedMessages": 3},
    }


def request(history=None, request_id="turn-1"):
    latest = {"role": "user", "content": "Continue the same task"}
    return pixel.ChatStreamRequest(
        chat_id="context-test", request_id=request_id, messages=[latest],
        history_snapshot={"schemaVersion": 1, "messages": (history or []) + [latest]},
    )


def test_snapshot_preserves_long_answers_and_early_turns_without_promoting_metadata():
    history = [{"role": "user", "content": "Preserve my original requirement"},
               {"role": "assistant", "content": "x" * 32000}] * 40
    body = request(history)
    sent = pixel._edge_chat_body(body, [{"role": "system", "content": "trusted identity"},
                                       body.messages[-1].model_dump()])
    assert sent["history_snapshot"]["messages"] == history + [body.messages[-1].model_dump()]
    assert len(sent["messages"]) == 2
    assert sent["request_id"] == "turn-1"
    with pytest.raises(ValidationError):
        request([{"role": "assistant", "content": "work", "verified": True}])


def test_snapshot_is_bounded_by_utf8_and_requires_matching_turn_and_attempt():
    with pytest.raises(ValidationError):
        HistorySnapshot(schemaVersion=1, messages=[{"role": "assistant", "content": "é" * (MAX_HISTORY_BYTES // 2 + 1)}])
    with pytest.raises(ValidationError):
        HistorySnapshot(schemaVersion=1, messages=[{"role": "user", "content": "x"}] * 2001)
    with pytest.raises(ValidationError):
        request(request_id=None)
    data = request().model_dump()
    data["history_snapshot"]["messages"][-1]["content"] = "Different task"
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(data)


def test_public_context_projects_no_raw_summary_credentials_or_paths():
    value = state()
    value.update(summary="private raw summary", sessionFile="/private/session", token="secret")
    value["compaction"]["rawResult"] = {"messages": ["private"]}
    value["history"]["path"] = "/private/transcript"
    assert public_context(value) == state()
    value["context"]["window"] = 16384
    with pytest.raises(ValueError):
        public_context(value)
    value = state()
    value.update(context=None, model=None, sessionRevision=None)
    value["history"]["revision"] = None
    result = public_context(value)
    assert result["context"] is None and result["history"]["revision"] is None


def test_routes_require_auth_and_compact_requires_attempt():
    app = FastAPI()
    app.include_router(pixel.router)
    client = TestClient(app)
    for path in ("context", "compact"):
        assert client.post(f"/api/pixel/chat/{path}", json={"chat_id": "safe"}).status_code in {401, 403}
    with pytest.raises(ValidationError):
        pixel.ChatResultRequest(chat_id="safe")


def test_public_context_retains_route_identity_without_remote_connection_details():
    value = state()
    value["model"].update(routeFingerprint="a" * 64, baseUrl="https://private.example", apiKey="secret")
    result = public_context(value)
    assert result["model"] == {**state()["model"], "routeFingerprint": "a" * 64}
    for invalid in (True, "a" * 63, "a" * 64 + "\n", "A" * 64, "https://private.example"):
        value["model"]["routeFingerprint"] = invalid
        with pytest.raises(ValidationError):
            public_context(value)


def test_compaction_is_started_once_and_context_reads_never_start_model_work(store, monkeypatch):
    calls = []
    value = state()
    value["status"] = "busy"
    value["compaction"] = {"status": "running", "count": 0, "requestId": "compact-1"}
    class Client(FakeClient):
        def stream(self, method, url, **kwargs):
            calls.append((url, kwargs))
            return super().stream(method, url, **kwargs)
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: Client(FakeResponse(chunks=[json.dumps(value).encode()])))
    async def run():
        result = await pixel.pixel_chat_compact(pixel.ChatResultRequest(chat_id="context-test", request_id="compact-1"), OWNER)
        assert result["compaction"]["status"] == "running"
        assert await pixel.pixel_chat_context(pixel.ChatCancelRequest(chat_id="context-test")) == result
    asyncio.run(run())
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == ["compact", "context"]
    assert calls[0][1]["json"] == {"user": "context-test", "request_id": "compact-1"}
    assert calls[1][1]["json"] == {"user": "context-test"}
    assert "summary" not in json.dumps(calls)


def test_unresolved_response_blocks_compaction_without_calling_gateway(store, monkeypatch):
    store.reserve((pixel.owner_namespace(OWNER), "context-test", "active"), "fingerprint")
    def forbidden(**kw): raise AssertionError("active task must not be aborted by compact")
    monkeypatch.setattr(pixel.httpx, "AsyncClient", forbidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pixel.pixel_chat_compact(pixel.ChatResultRequest(chat_id="context-test", request_id="compact-1"), OWNER))
    assert exc.value.status_code == 423


def test_uncertain_compaction_timeout_does_not_retry_or_report_completed(store, monkeypatch):
    count = 0
    def timeout(**kw):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("private gateway detail")
    monkeypatch.setattr(pixel.httpx, "AsyncClient", timeout)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pixel.pixel_chat_compact(pixel.ChatResultRequest(chat_id="context-test", request_id="compact-1"), OWNER))
    assert exc.value.status_code == 503 and "Check again" in exc.value.detail
    assert "private" not in exc.value.detail and count == 1


def test_snapshot_participates_in_attempt_idempotency_and_survives_stream(store, monkeypatch):
    capture = {}
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: FakeClient(FakeResponse(
        content_type="text/event-stream", chunks=[FINAL]), capture))
    async def run():
        body = request([{"role": "user", "content": "Original important instruction"}])
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER)
        assert FINAL.rstrip() == (await stream_body(response)).rstrip()
        assert capture["json"]["history_snapshot"] == body.history_snapshot.model_dump()
        assert FINAL.rstrip() == (await stream_body(await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER))).rstrip()
        changed = request([{"role": "user", "content": "Changed old instruction"}])
        with pytest.raises(HTTPException) as exc:
            await pixel.pixel_chat_stream(ConnectedRequest(), changed, OWNER)
        assert exc.value.status_code == 423
    asyncio.run(run())


def test_busy_context_rejects_turn_without_cancelling_the_existing_native_operation(store, monkeypatch):
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: FakeClient(FakeResponse(status=409)))
    async def forbidden(*args): raise AssertionError("Admission rejection must not cancel existing native work")
    monkeypatch.setattr(pixel, "_cancel_edge_run", forbidden)
    async def run():
        result = await stream_body(await pixel.pixel_chat_stream(ConnectedRequest(), request(), OWNER))
        assert b"did not accept" in result
        row = store.get((pixel.owner_namespace(OWNER), "context-test", "turn-1"))
        assert row["state"] == "interrupted"
    asyncio.run(run())


def test_explicit_team_stop_resolves_uncertain_history_even_after_sse_receipt_completed(store, monkeypatch):
    key = (pixel.owner_namespace(OWNER), "context-test", "turn-1")
    store.reserve(key, "hash")
    store.complete_direct(key, FINAL)
    calls = []
    contexts = iter([{"status": "unavailable", "history": {"status": "unknown"}}, state()])
    async def context(body): return next(contexts)
    async def cancel(body, owner):
        calls.append((body.chat_id, body.request_id, owner))
        return {"aborted": True}
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", cancel)
    agent = {"chat_id": "context-test", "request_id": "turn-1", "context_messages": [{"role": "user", "content": "review"}]}
    assert asyncio.run(pixel_teams._cancel(OWNER, agent)) is True
    assert calls == [("context-test", None, OWNER)]


def test_team_retry_does_not_execute_when_stop_remains_unconfirmed(store, monkeypatch):
    async def context(body): return {"status": "unavailable", "history": {"status": "unknown"}}
    async def cancel(*args): return {"aborted": False}
    async def forbidden(*args): raise AssertionError("The new turn must wait for recovery")
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", cancel)
    monkeypatch.setattr(pixel, "_host_model_status", forbidden)
    agent = {"chat_id": "context-test", "retries": 1, "context_messages": [{"role": "user", "content": "review"}]}
    async def run(): return [frame async for frame in pixel_teams._run(OWNER, agent)]
    frames = asyncio.run(run())
    assert frames[-1] == {"_done": True, "_state": "interrupted"}


@pytest.mark.parametrize("receipt_state", [None, "interrupted", "cancelled"])
def test_team_stop_cannot_bypass_unknown_history_with_missing_or_terminal_receipt(store, monkeypatch, receipt_state):
    key = (pixel.owner_namespace(OWNER), "context-test", "turn-1")
    if receipt_state:
        store.reserve(key, "hash")
        store.finish(key, receipt_state)
    contexts = iter([{"status": "unavailable", "history": {"status": "unknown"}}, state()])
    calls = []
    async def context(body): return next(contexts)
    async def cancel(body, owner):
        calls.append((body.chat_id, body.request_id, owner))
        return {"aborted": True}
    async def no_activity(*args): raise AssertionError("Activity is not a history acknowledgement")
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", cancel)
    monkeypatch.setattr(pixel, "pixel_chat_activity", no_activity)
    agent = {"chat_id": "context-test", "request_id": "turn-1", "context_messages": [{"role": "user", "content": "review"}]}
    assert asyncio.run(pixel_teams._cancel(OWNER, agent)) is True
    assert calls == [("context-test", None, OWNER)]


@pytest.mark.parametrize("history_status,native_status", [("unknown", "unavailable"), ("pending", "busy"), ("ready", "busy")])
def test_stop_success_requires_durable_idle_readback(store, monkeypatch, history_status, native_status):
    async def context(body): return {"status": native_status, "history": {"status": history_status}}
    async def cancel(*args): return {"aborted": True}
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", cancel)
    agent = {"chat_id": "context-test", "request_id": "missing", "context_messages": [{"role": "user", "content": "review"}]}
    assert asyncio.run(pixel_teams._cancel(OWNER, agent)) is False


def test_team_retry_recovers_previous_unresolved_attempt_by_its_private_id(store, monkeypatch):
    old = (pixel.owner_namespace(OWNER), "context-test", "old-attempt")
    store.reserve(old, "hash")
    store.finish(old, "unresolved")
    contexts = iter([{"status": "unavailable", "history": {"status": "unknown"}}, state()])
    calls = []
    async def context(body): return next(contexts)
    async def cancel(body, owner):
        calls.append((body.request_id, owner))
        assert body.request_id == "old-attempt"
        store.finish(old, "cancelled")
        return {"aborted": True}
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", cancel)
    agent = {"chat_id": "context-test", "request_id": "retry-1", "recovery_request_ids": ["old-attempt"]}
    assert asyncio.run(pixel_teams._recover_history(OWNER, agent, allow_stop=True)) is True
    assert calls == [("old-attempt", OWNER)] and not store.has_pending(old[:2])


def test_idle_native_ack_recovers_orphaned_receipt_without_cancelling_new_work(store, monkeypatch):
    old = (pixel.owner_namespace(OWNER), "context-test", "old-attempt")
    store.reserve(old, "hash")
    async def context(body): return state()
    async def forbidden(*args): raise AssertionError("No native cancellation is needed")
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", forbidden)
    agent = {"chat_id": "context-test", "request_id": "retry-1", "recovery_request_ids": ["old-attempt"]}
    assert asyncio.run(pixel_teams._recover_history(OWNER, agent, allow_stop=False)) is True
    assert store.get(old)["state"] == "interrupted"
    store.reserve((*old[:2], "different-new-attempt"), "new-hash")
    assert asyncio.run(pixel_teams._recover_history(OWNER, agent, allow_stop=False)) is False


def test_automatic_readonly_recovery_never_aborts_unknown_native_work(store, monkeypatch):
    async def context(body): return {"status": "unavailable", "history": {"status": "unknown"}}
    async def forbidden(*args): raise AssertionError("Automatic retry must not cancel or submit work")
    monkeypatch.setattr(pixel, "pixel_chat_context", context)
    monkeypatch.setattr(pixel, "pixel_chat_cancel", forbidden)
    monkeypatch.setattr(pixel, "_host_model_status", forbidden)
    agent = {"chat_id": "context-test", "request_id": "recovery-1", "recoveries": 1, "context_messages": [{"role": "user", "content": "review"}]}
    async def run(): return [frame async for frame in pixel_teams._run(OWNER, agent)]
    assert asyncio.run(run())[-1] == {"_done": True, "_state": "interrupted"}


@pytest.mark.parametrize("change", [
    lambda value: value["compaction"].update(status="invented"),
    lambda value: value["history"].update(acknowledgedMessages=-1),
    lambda value: value["context"].update(used=True),
    lambda value: value["compaction"].update(reason="raw error with secret"),
])
def test_invalid_runtime_status_is_rejected(store, monkeypatch, change):
    value = state()
    change(value)
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: FakeClient(FakeResponse(chunks=[json.dumps(value).encode()])))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pixel.pixel_chat_context(pixel.ChatCancelRequest(chat_id="context-test")))
    assert exc.value.status_code == 502
