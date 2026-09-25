"""Interrupted streaming responses retain every observed token category."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]


def load(filename):
    spec = importlib.util.spec_from_file_location(f"interrupted_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "receipt-fixture-key")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-upstream-key")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    db.init_db()
    api = load("main.py")
    api.SETTINGS_PATH = str(tmp_path / "settings.json")
    yield api
    db._local.conn.close()


CASES = [
    ("anthropic", "input_tokens", "input_tokens", 7),
    ("anthropic", "cache_read_input_tokens", "cache_read_tokens", 1024),
    ("anthropic", "cache_creation_input_tokens", "cache_write_tokens", 1024),
    ("anthropic", "output_tokens", "output_tokens", 9),
    ("openai", "completion_tokens", "output_tokens", 9),
]


@pytest.mark.parametrize("protocol,wire_key,stored_key,count", CASES)
@pytest.mark.parametrize("ending", ["eof", "read-error", "cancel", "complete"])
def test_observed_usage_survives_stream_termination(proxy, monkeypatch, protocol, wire_key, stored_key, count, ending):
    api = proxy
    path = "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions"
    getter = "get_http_client" if protocol == "anthropic" else "get_moonshot_client"

    async def exercise():
        observed = asyncio.Event()
        if protocol == "anthropic":
            payload = (
                'event: message_start\ndata: {"message":{"usage":{"input_tokens":0}}}\n\n'
                + "event: message_delta\ndata: " + json.dumps({"usage": {wire_key: count}, "delta": {}}) + "\n\n"
            )
            terminal = "event: message_stop\ndata: {}\n\n"
        else:
            payload = "data: " + json.dumps({"usage": {wire_key: count}, "choices": []}) + "\n\n"
            terminal = "data: [DONE]\n\n"

        class Upstream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield payload.encode()
                observed.set()
                if ending == "read-error":
                    raise httpx.ReadError("fixture transport interrupted")
                if ending == "cancel":
                    await asyncio.Future()
                if ending == "complete":
                    yield terminal.encode()

        async with httpx.AsyncClient(base_url="https://provider.example",
                transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Upstream()))) as upstream:
            monkeypatch.setattr(api, getter, lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer receipt-fixture-key"}) as client:
                request = asyncio.create_task(client.post(path, json={
                    "model": "unpriced-fixture-model", "stream": True,
                    "messages": [{"role": "user", "content": "Hello"}]}))
                if ending == "cancel":
                    await asyncio.wait_for(observed.wait(), timeout=2)
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request
                else:
                    response = await asyncio.wait_for(request, timeout=2)
                    assert response.status_code == 200
                    assert response.text == payload + (terminal if ending == "complete" else "")
                today = datetime.now(timezone.utc).date().isoformat()
                response = await client.get("/api/report", params={"start": today, "end": today})
                assert response.status_code == 200, response.text
                report = response.json()
                assert report["summary"]["requests"] == 1
                assert report["summary"][stored_key] == count
                assert report["summary"]["total_tokens"] == count
    asyncio.run(exercise())


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_disconnect_before_usage_does_not_create_phantom_request(proxy, monkeypatch, protocol):
    api = proxy
    path = "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions"
    getter = "get_http_client" if protocol == "anthropic" else "get_moonshot_client"

    async def exercise():
        async with httpx.AsyncClient(base_url="https://provider.example",
                transport=httpx.MockTransport(lambda _: httpx.Response(200, content=": keepalive\n\n"))) as upstream:
            monkeypatch.setattr(api, getter, lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer receipt-fixture-key"}) as client:
                response = await client.post(path, json={
                    "model": "fixture", "stream": True, "messages": []})
                assert response.status_code == 200
                today = datetime.now(timezone.utc).date().isoformat()
                report = await client.get("/api/report", params={"start": today, "end": today})
                assert report.json()["summary"]["requests"] == 0
    asyncio.run(exercise())
