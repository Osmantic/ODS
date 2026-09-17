"""A contended usage database must not freeze other proxy HTTP requests."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

import httpx
import pytest

from test_usage_report import load_sqlite_db


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("stream", [False, True])
def test_health_remains_available_during_proxy_sqlite_write(tmp_path, monkeypatch, provider, stream):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "persistence-fixture")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-upstream-key")
    spec = importlib.util.spec_from_file_location(f"proxy_io_{uuid4().hex}", service / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.app.dependency_overrides[api.verify_api_key] = lambda: "persistence-fixture"
    monkeypatch.setattr(api, "AGENT_SESSION_DIRS", {})
    db = load_sqlite_db(tmp_path, monkeypatch)
    entered, release, locked, unlocked = (threading.Event() for _ in range(4))

    def hold_write_lock():
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            # Rescue the blocking baseline without relying on a latency threshold.
            release.wait(3)
            connection.commit()
        unlocked.set()

    def record(entry):
        entered.set()
        try:
            db.log_usage(entry)
        finally:
            db._get_conn().close()
            db._local.conn = None

    monkeypatch.setattr(api, "log_usage", record)
    holder = threading.Thread(target=hold_write_lock)
    holder.start()
    assert locked.wait(3)
    if provider == "anthropic":
        path = "/v1/messages"
        result = {"usage": {"input_tokens": 12, "output_tokens": 3}, "stop_reason": "end_turn"}
        events = 'event: message_start\ndata: ' + json.dumps({"message": result})
        events += '\n\nevent: message_delta\ndata: {"usage":{"output_tokens":3}}'
        events += '\n\nevent: message_stop\ndata: {}\n\n'
    else:
        path = "/v1/chat/completions"
        result = {"usage": {"prompt_tokens": 12, "completion_tokens": 3}, "choices": []}
        events = 'data: ' + json.dumps(result) + '\n\ndata: [DONE]\n\n'

    async def upstream_handler(request):
        assert request.url.path == path
        if stream:
            return httpx.Response(200, text=events, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=result)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler),
                                    base_url="http://upstream.test") as upstream:
            monkeypatch.setattr(api, "get_http_client", lambda: upstream)
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                        base_url="http://proxy.test") as client:
                request = asyncio.create_task(client.post(path, json={
                    "model": "fixture-model", "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 10, "stream": stream,
                }))
                try:
                    assert await asyncio.to_thread(entered.wait, 4)
                    health = await client.get("/health")
                    assert health.status_code == 200
                    assert not unlocked.is_set(), "health waited for the SQLite write lock"
                finally:
                    release.set()
                    response = await request
                assert response.status_code == 200
                assert response.text == events if stream else response.json() == result

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        holder.join(4)
        assert not holder.is_alive()
    rows = db.query_usage()
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 12
    assert rows[0]["output_tokens"] == 3


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("disconnect_at", ["partial", "logging"])
def test_disconnected_stream_records_usage_once(tmp_path, monkeypatch, provider, disconnect_at):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "disconnect-fixture")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-upstream-key")
    spec = importlib.util.spec_from_file_location(f"disconnect_{uuid4().hex}", service / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.app.dependency_overrides[api.verify_api_key] = lambda: "disconnect-fixture"
    monkeypatch.setattr(api, "AGENT_SESSION_DIRS", {})
    db = load_sqlite_db(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def record(entry):
        entered.set()
        assert release.wait(4), "test did not release the billing writer"
        try:
            db.log_usage(entry)
        finally:
            db._get_conn().close()
            db._local.conn = None

    monkeypatch.setattr(api, "log_usage", record)

    async def exercise():
        disconnect, delivered, partial = (asyncio.Event() for _ in range(3))
        if provider == "anthropic":
            path = "/v1/messages"
            prefix = b'event: message_start\ndata: {"message":{"usage":{"input_tokens":12}}}\n\n'
            terminal = b'event: message_stop\ndata: {}\n\n'
        else:
            path = "/v1/chat/completions"
            prefix = b'data: {"usage":{"prompt_tokens":12},"choices":[]}\n\n'
            terminal = b'data: [DONE]\n\n'

        class UpstreamBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield prefix
                if disconnect_at == "partial":
                    partial.set()
                    await asyncio.Event().wait()
                yield terminal

        async with httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=UpstreamBody()),
        )) as upstream:
            monkeypatch.setattr(api, "get_http_client", lambda: upstream)
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            body = json.dumps({"model": "fixture", "messages": [], "stream": True}).encode()
            body_sent = False
            responses = []

            async def receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await disconnect.wait()
                delivered.set()
                return {"type": "http.disconnect"}

            async def send(message):
                responses.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                     "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                     "raw_path": path.encode(), "query_string": b"", "root_path": "",
                     "headers": [(b"content-type", b"application/json")],
                     "client": ("127.0.0.1", 1234), "server": ("proxy.test", 80)}
            request = asyncio.create_task(api.app(scope, receive, send))
            try:
                if disconnect_at == "partial":
                    await asyncio.wait_for(partial.wait(), 3)
                else:
                    assert await asyncio.to_thread(entered.wait, 3)
                disconnect.set()
                await asyncio.wait_for(delivered.wait(), 3)
                assert await asyncio.to_thread(entered.wait, 3)
                assert not request.done(), "disconnect abandoned billing"
            finally:
                release.set()
                disconnect.set()
                await asyncio.wait_for(request, 4)
            assert responses[0]["status"] == 200

    asyncio.run(exercise())
    rows = db.query_usage()
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 12
