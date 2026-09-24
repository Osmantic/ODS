"""Exercise owner upload admission through HTTP, before host mutations."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import request_body
from routers import (
    pixel_advice, pixel_advice_runtime, pixel_handoff, pixel_providers,
    pixel_scopes, pixel_settings, portal_identity,
)

CASES = [
    (pixel_advice, "/api/pixel/advice/status", 128 * 1024),
    (pixel_advice_runtime, "/api/pixel/advice-runtime/status", 8192),
    (pixel_handoff, "/api/pixel/handoff/list", 4096),
    (pixel_scopes, "/api/pixel/provider-scopes/status", 4096),
    (pixel_settings, "/api/pixel/settings/save", 256 * 1024),
    (pixel_settings, "/api/pixel/settings/runtime", 256 * 1024),
    (pixel_providers, "/api/pixel/providers/save", 256 * 1024),
    (pixel_providers, "/api/pixel/providers/runtime", 2048),
    (pixel_providers, "/api/pixel/providers/connection-probe", 65536),
    (portal_identity, "/api/pixel/identity/save", 2048),
]
KEY = {"Authorization": "Bearer test-key-12345"}


@pytest.fixture(params=CASES, ids=[case[1] for case in CASES])
def route(request, monkeypatch):
    module, path, limit = request.param
    app = FastAPI()
    app.include_router(module.router)
    host = AsyncMock()
    monkeypatch.setattr(module, "request_agent_json", host)
    monkeypatch.setattr(request_body, "BODY_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr("security.DASHBOARD_API_KEY", "test-key-12345")
    return app, path, limit, host


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [b"", b"{"])
async def test_stalled_upload_returns_408_without_forwarding(route, first_chunk):
    app, path, _, host = route
    cancelled = asyncio.Event()

    async def stalled():
        try:
            if first_chunk:
                yield first_chunk
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await asyncio.wait_for(client.post(path, content=stalled(), headers=KEY), 0.5)
    assert response.status_code == 408
    assert response.headers["cache-control"] == "no-store"
    assert cancelled.is_set()
    host.assert_not_awaited()


@pytest.mark.asyncio
async def test_size_limit_and_authentication_still_precede_host_calls(route):
    app, path, limit, host = route
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=b" " * (limit + 1), headers=KEY)
        assert response.status_code == 413
        response = await client.post(path, content=b"{}")
        assert response.status_code == 401
    host.assert_not_awaited()
