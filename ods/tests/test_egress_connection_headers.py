"""Exercise egress header forwarding through its ASGI HTTP boundary."""
import importlib.util
import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


@pytest.fixture
def egress(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "egress_connection_test_app",
        ROOT / "extensions/services/remote-provider-egress/app/main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_load_route", lambda: {
        "enabled": True, "transport": "direct",
        "provider": {"baseUrl": "https://provider.example/v1", "model": "real-model"},
    })
    monkeypatch.setattr(module, "validate_direct_provider_resolution", lambda route: [])
    monkeypatch.setattr(module, "read_provider_secret", lambda path: "provider-token")
    yield module


@pytest.mark.parametrize("endpoint", ["chat/completions", "completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_request_connection_options_do_not_reach_provider(egress, monkeypatch, endpoint, stream):
    seen = []

    def provider(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/" + endpoint, json={"model": "ods/current", "stream": stream},
                               headers=[
                                   ("Connection", " X-Hop-One , authorization, content-type "),
                                   ("Connection", "x-HOP-two"),
                                   ("X-Hop-One", "local-only-one"),
                                   ("X-Hop-Two", "local-only-two"),
                                   ("Authorization", "Bearer caller-token"),
                                   ("X-Request-ID", "retained"),
                               ])
        client.portal.call(transport.aclose)
    assert response.status_code == 200
    assert len(seen) == 1
    headers = seen[0].headers
    assert "x-hop-one" not in headers
    assert "x-hop-two" not in headers
    assert headers["authorization"] == "Bearer provider-token"
    assert headers["content-type"] == "application/json"
    assert headers["x-request-id"] == "retained"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [200, 429])
def test_response_connection_options_stay_on_provider_hop(egress, monkeypatch, stream, status):
    def provider(request):
        return httpx.Response(status, content=b'{"ok": true}', headers=[
            ("Connection", " x-hop-one, content-type "),
            ("Connection", "X-HOP-TWO"),
            ("X-Hop-One", "internal-one"), ("X-Hop-Two", "internal-two"),
            ("Content-Type", "application/provider-local"),
            ("X-Request-ID", "retained"), ("Retry-After", "7"),
        ])

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/chat/completions", json={"stream": stream})
        client.portal.call(transport.aclose)
    assert response.status_code == status
    assert response.content == b'{"ok": true}'
    assert "x-hop-one" not in response.headers
    assert "x-hop-two" not in response.headers
    assert response.headers.get("content-type") != "application/provider-local"
    assert response.headers["x-request-id"] == "retained"
    assert response.headers["retry-after"] == "7"
    assert response.headers["x-ods-provider-model"] == "real-model"


def test_streaming_response_enforces_total_upstream_deadline(egress, monkeypatch):
    egress.UPSTREAM_TIMEOUT_SECONDS = 0.04
    closed = []

    async def slow_body():
        try:
            while True:
                await asyncio.sleep(0.015)
                yield b"data: keep-alive\n\n"
        finally:
            closed.append(True)

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            async for chunk in slow_body():
                yield chunk

        async def aclose(self):
            closed.append(True)

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, stream=SlowBody())

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=SlowTransport())
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        started = time.monotonic()
        response = client.post("/v1/chat/completions", json={"stream": True})
        elapsed = time.monotonic() - started
        client.portal.call(transport.aclose)

    assert response.status_code == 200
    assert elapsed < 0.3
    assert closed
