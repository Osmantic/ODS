"""Exercise real durable receipts plus asynchronous upstream/disconnect races."""
import asyncio
import os
import sqlite3
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pixel_chat_results as receipts
from routers import pixel
from test_pixel import FakeClient, FakeResponse, ConnectedRequest, DisconnectedRequest, stream_body

OWNER = "dashboard-test-key"
IDENTITY = (receipts.owner_namespace(OWNER), "chat-test", "attempt-one")
FINAL = b'data: {"choices":[{"delta":{"content":"Saved result"}}]}\n\ndata: [DONE]\n\n'


@pytest.fixture
def store(tmp_path, monkeypatch):
    result = receipts.ChatResultStore(tmp_path / "receipts")
    monkeypatch.setattr(pixel, "_result_store", result)
    monkeypatch.setattr(pixel, "_result_tasks", {})
    monkeypatch.setattr(pixel, "_result_stops", set())
    monkeypatch.setattr(pixel, "_result_abort_ack", set())
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "e" * 64)
    async def ready(): return None
    monkeypatch.setattr(pixel, "_model_readiness_issue", ready)
    yield result
    result.close()


def body(request="attempt-one", text="Do work"):
    return pixel.ChatStreamRequest(chat_id="chat-test", request_id=request, messages=[{"role":"user", "content":text}])


def test_receipt_survives_restart_without_reexecuting_and_scopes_owner(store, tmp_path):
    store.reserve(IDENTITY, "input-hash")
    store.append(IDENTITY, FINAL)
    store.finish(IDENTITY, "complete")
    other = receipts.ChatResultStore(tmp_path / "receipts")
    try:
        assert other.get(IDENTITY)["state"] == "complete"
        assert other.chunks(IDENTITY)[0]["data"] == FINAL
        assert other.get((receipts.owner_namespace("other-owner"), *IDENTITY[1:])) is None
        assert other.reserve(IDENTITY, "input-hash") is False
        with pytest.raises(receipts.ResultConflict): other.reserve(IDENTITY, "changed")
    finally: other.close()


def test_restart_does_not_adopt_or_duplicate_unfinished_work(store, tmp_path):
    store.reserve(IDENTITY, "hash")
    other = receipts.ChatResultStore(tmp_path / "receipts")
    try:
        assert other.get(IDENTITY)["state"] == "unresolved"
        with pytest.raises(receipts.ResultConflict): other.reserve((*IDENTITY[:2], "next"), "hash")
        other.finish(IDENTITY, "cancelled")
        assert other.reserve((*IDENTITY[:2], "next"), "hash") is True
    finally: other.close()


def test_bounds_preserve_existing_results_and_leave_room_for_terminal_error(store, monkeypatch):
    monkeypatch.setattr(receipts, "MAX_RESULT_BYTES", 5000)
    store.reserve(IDENTITY, "hash")
    store.append(IDENTITY, b"x" * 900)
    with pytest.raises(receipts.ResultCapacity): store.append(IDENTITY, b"overflow")
    store.append(IDENTITY, b"data: [DONE]\n\n", terminal=True)
    assert store.chunks(IDENTITY)[0]["data"] == b"x" * 900


@pytest.mark.skipif(os.name != "posix", reason="POSIX custody contract")
def test_unsafe_storage_paths_are_rejected(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError): receipts.ChatResultStore(public)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("do not overwrite")
    (private / "results.sqlite3").symlink_to(target)
    with pytest.raises(ValueError): receipts.ChatResultStore(private)
    assert target.read_text() == "do not overwrite"


def test_disconnect_keeps_one_producer_and_replay_is_repeatable(store, monkeypatch):
    async def run():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []
        cancels = []
        class Upstream(FakeResponse):
            async def aiter_bytes(self):
                started.set()
                await release.wait()
                yield FINAL
        class Client(FakeClient):
            def stream(self, *args, **kwargs):
                calls.append((args, kwargs))
                return super().stream(*args, **kwargs)
        monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: Client(Upstream(content_type="text/event-stream")))
        async def cancel(*args):
            cancels.append(args)
            return True
        monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)
        response = await pixel.pixel_chat_stream(DisconnectedRequest(), body(), OWNER)
        await started.wait()
        assert await stream_body(response) == b""
        assert store.get(IDENTITY)["state"] == "active"
        duplicate = await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        assert len(calls) == 1
        with pytest.raises(HTTPException) as changed:
            await pixel.pixel_chat_stream(ConnectedRequest(), body(text="different"), OWNER)
        assert changed.value.status_code == 423
        release.set()
        await asyncio.gather(*list(pixel._result_tasks.values()))
        replay = await stream_body(duplicate)
        assert b"Saved result" in replay and b"[DONE]" in replay
        query = pixel.ChatResultRequest(chat_id="chat-test", request_id="attempt-one")
        first = await pixel.pixel_chat_result(query, OWNER)
        assert first == await pixel.pixel_chat_result(query, OWNER)
        assert first["state"] == "complete" and first["events"].encode() == replay
        assert await pixel.pixel_chat_result(query, "other-owner") == {"state":"unknown", "events":""}
        assert len(calls) == 1 and not cancels
    asyncio.run(run())


def test_stop_blocks_new_attempt_until_ack_and_stale_stop_never_cancels_successor(store, monkeypatch):
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        permit_cancel = asyncio.Event()
        cancel_entered = asyncio.Event()
        count = 0
        class Upstream(FakeResponse):
            async def aiter_bytes(self):
                started.set()
                try: await asyncio.Future()
                finally: cancelled.set()
                yield b""
        monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: FakeClient(Upstream(content_type="text/event-stream")))
        async def cancel(*args):
            nonlocal count
            count += 1
            cancel_entered.set()
            await permit_cancel.wait()
            return True
        monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)
        await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await started.wait()
        query = pixel.ChatCancelRequest(chat_id="chat-test", request_id="attempt-one")
        stop = asyncio.create_task(pixel.pixel_chat_cancel(query, OWNER))
        await cancel_entered.wait()
        with pytest.raises(HTTPException): await pixel.pixel_chat_stream(ConnectedRequest(), body("two"), OWNER)
        permit_cancel.set()
        assert await stop == {"aborted":True}
        await cancelled.wait()
        await pixel.pixel_chat_stream(ConnectedRequest(), body("two"), OWNER)
        assert await pixel.pixel_chat_cancel(query, OWNER) == {"aborted":False}
        assert await pixel.pixel_chat_cancel(pixel.ChatCancelRequest(chat_id="chat-test"), OWNER) == {"aborted":False}
        assert count == 1
        assert await pixel.pixel_chat_cancel(pixel.ChatCancelRequest(chat_id="chat-test", request_id="two"), OWNER) == {"aborted":True}
    asyncio.run(run())


def test_result_and_cancel_require_owner_authentication(store):
    app = FastAPI()
    app.include_router(pixel.router)
    with TestClient(app) as client:
        for endpoint in ["result", "cancel"]:
            payload = {"chat_id":"chat-test", "request_id":"attempt-one"}
            assert client.post('/api/pixel/chat/'+endpoint, json=payload).status_code == 401
            assert client.post('/api/pixel/chat/'+endpoint, json=payload, headers={"Authorization":"Bearer wrong"}).status_code == 403


@pytest.mark.parametrize("ack", [True, False])
def test_truncated_upstream_retains_error_and_does_not_release_unknown_native_work(store, monkeypatch, ack):
    async def run():
        monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kw: FakeClient(FakeResponse(content_type="text/event-stream", chunks=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'])))
        async def cancel(*args): return ack
        monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)
        await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await asyncio.gather(*list(pixel._result_tasks.values()))
        assert store.get(IDENTITY)["state"] == ("interrupted" if ack else "unresolved")
        assert store.has_pending(IDENTITY[:2]) is not ack
        data = b''.join(row['data'] for row in store.chunks(IDENTITY))
        assert b'partial' in data and b'pixel_dashboard_error' in data and b'[DONE]' in data
    asyncio.run(run())


def test_terminal_write_failure_still_releases_known_stopped_attempt(store, monkeypatch):
    async def run():
        append = store.append
        def failing_append(key, data, **kwargs):
            if kwargs.get('terminal'): raise sqlite3.OperationalError('disk full')
            return append(key, data, **kwargs)
        monkeypatch.setattr(store, 'append', failing_append)
        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: FakeClient(FakeResponse(content_type='text/event-stream')))
        async def cancel(*args): return True
        monkeypatch.setattr(pixel, '_cancel_edge_run', cancel)
        await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await asyncio.gather(*list(pixel._result_tasks.values()), return_exceptions=True)
        assert store.get(IDENTITY)['state'] == 'interrupted'
        assert not store.has_pending(IDENTITY[:2])
    asyncio.run(run())


def test_finished_task_with_failed_state_commit_is_not_reported_as_running(store, monkeypatch):
    store.reserve(IDENTITY, 'hash')
    async def terminal(*args): return {'state':'terminal'}
    monkeypatch.setattr(pixel, 'pixel_chat_activity', terminal)
    result = asyncio.run(pixel.pixel_chat_result(pixel.ChatResultRequest(chat_id='chat-test',request_id='attempt-one'), OWNER))
    assert result == {'state':'interrupted','events':''}
    assert not store.has_pending(IDENTITY[:2])


def test_orphaned_receipts_do_not_reserve_future_output_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts,'MAX_ACTIVE',1)
    first = receipts.ChatResultStore(tmp_path/'private')
    first.reserve(IDENTITY,'hash')
    first.close()
    second = receipts.ChatResultStore(tmp_path/'private')
    try:
        assert second.reserve((IDENTITY[0],'different-chat','next'),'hash')
        assert second.get(IDENTITY)['state'] == 'unresolved'
    finally: second.close()


def test_api_task_shutdown_is_not_reported_as_owner_stop(store, monkeypatch):
    async def run():
        started = asyncio.Event()
        class Upstream(FakeResponse):
            async def aiter_bytes(self):
                started.set()
                await asyncio.Future()
                yield b''
        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: FakeClient(Upstream(content_type='text/event-stream')))
        async def cancel(*args): return True
        monkeypatch.setattr(pixel, '_cancel_edge_run', cancel)
        await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await started.wait()
        task = pixel._result_tasks[IDENTITY]
        task.cancel()
        await task
        assert store.get(IDENTITY)['state'] == 'interrupted'
        assert b'Pixel was stopped' not in b''.join(row['data'] for row in store.chunks(IDENTITY))
    asyncio.run(run())


def test_completed_stream_wins_stop_during_upstream_context_teardown(store, monkeypatch):
    async def run():
        closing = asyncio.Event()
        class Client(FakeClient):
            async def __aexit__(self, *args):
                closing.set()
                await asyncio.Future()
        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: Client(FakeResponse(content_type='text/event-stream', chunks=[FINAL])))
        async def cancel(*args): return True
        monkeypatch.setattr(pixel, '_cancel_edge_run', cancel)
        await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await asyncio.wait_for(closing.wait(), 1)
        assert store.get(IDENTITY)['state'] == 'active'
        result = await pixel.pixel_chat_cancel(pixel.ChatCancelRequest(chat_id='chat-test', request_id='attempt-one'), OWNER)
        assert result == {'aborted': False}
        assert store.get(IDENTITY)['state'] == 'complete'
        assert b'Saved result' in b''.join(row['data'] for row in store.chunks(IDENTITY))
    asyncio.run(run())
