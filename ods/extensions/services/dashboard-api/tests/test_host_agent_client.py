from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

import host_agent_client as agent_client


def test_host_agent_url_usage_is_centralized():
    source_root = Path(__file__).resolve().parents[1]
    users = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts and "AGENT_URL" in path.read_text(encoding="utf-8")
    }

    assert users == {"config.py", "host_agent_client.py", "main.py"}


def test_sync_client_is_singleton_with_bounded_limits(monkeypatch):
    created = []

    class _Client:
        is_closed = False

    def factory(**kwargs):
        created.append(kwargs)
        return _Client()

    monkeypatch.setattr(agent_client, "_sync_client", None)
    monkeypatch.setattr(agent_client.httpx, "Client", factory)
    with ThreadPoolExecutor(max_workers=16) as pool:
        clients = list(pool.map(lambda _: agent_client._get_sync_client(), range(32)))

    assert len(created) == 1
    assert all(client is clients[0] for client in clients)
    assert created[0]["trust_env"] is False
    assert created[0]["limits"].max_connections == 8
    assert created[0]["limits"].max_keepalive_connections == 4


@pytest.mark.asyncio
async def test_async_client_is_singleton_with_bounded_limits(monkeypatch):
    created = []

    class _Client:
        is_closed = False

    def factory(**kwargs):
        created.append(kwargs)
        return _Client()

    monkeypatch.setattr(agent_client, "_async_client", None)
    monkeypatch.setattr(agent_client.httpx, "AsyncClient", factory)
    clients = await asyncio.gather(*(agent_client._get_async_client() for _ in range(32)))

    assert len(created) == 1
    assert all(client is clients[0] for client in clients)
    assert created[0]["trust_env"] is False
    assert created[0]["limits"].max_connections == 2
    assert created[0]["limits"].max_keepalive_connections == 2


def test_get_retries_one_stale_connection_and_uses_split_timeout(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError("stale keep-alive", request=request)
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        assert agent_client.request_json("GET", "/health", timeout=600) == {"status": "ok"}
        assert len(calls) == 2
        assert calls[-1].extensions["timeout"] == {
            "connect": 5.0,
            "read": 600.0,
            "write": 30.0,
            "pool": 5.0,
        }
    finally:
        client.close()


def test_post_never_retries_transport_failure(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("connection lost", request=request)

    client = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(agent_client.AgentUnavailable):
            agent_client.request_json("POST", "/v1/model/activate", payload={"model": "x"})
        assert calls == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(409, json={"error": "busy"}), agent_client.AgentHTTPError),
        (httpx.Response(200, text="not-json"), agent_client.AgentProtocolError),
        (httpx.Response(200, json=["not", "an", "object"]), agent_client.AgentProtocolError),
    ],
)
def test_response_error_mapping(monkeypatch, response, error_type):
    def handler(request: httpx.Request) -> httpx.Response:
        response.request = request
        return response

    client = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(error_type) as caught:
            agent_client.request_json("GET", "/v1/model/status")
        if isinstance(caught.value, agent_client.AgentHTTPError):
            assert caught.value.status_code == 409
            assert caught.value.detail == "busy"
    finally:
        client.close()


def test_timeout_is_distinct_from_unavailable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(agent_client.AgentTimeout):
            agent_client.request_json("GET", "/health")
    finally:
        client.close()


@pytest.mark.asyncio
async def test_async_request_and_shutdown_close_both_pools(monkeypatch):
    async_client = httpx.AsyncClient(
        base_url="http://agent",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
    )
    sync_client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
    )
    monkeypatch.setattr(agent_client, "_async_client", async_client)
    monkeypatch.setattr(agent_client, "_sync_client", sync_client)

    assert await agent_client.async_request_json("GET", "/health") == {"ok": True}
    await agent_client.shutdown_clients()

    assert async_client.is_closed
    assert sync_client.is_closed
    assert agent_client._async_client is None
    assert agent_client._sync_client is None
