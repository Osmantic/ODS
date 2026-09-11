"""The application lifetime owns the pooled LLM HTTP client."""

from unittest.mock import AsyncMock

import pytest

import helpers
import main


@pytest.mark.asyncio
@pytest.mark.parametrize("health_failure", [False, True])
async def test_lifespan_closes_llm_pool_and_allows_a_fresh_lifetime(monkeypatch, health_failure):
    monkeypatch.setattr(helpers, "_httpx_client", None)
    monkeypatch.setattr(helpers, "_httpx_client_lock", None)
    for name in ("collect_metrics", "_poll_service_health", "shutdown_agent_clients"):
        monkeypatch.setattr(main, name, AsyncMock())
    monkeypatch.setattr(main.gpu_router, "poll_gpu_history", AsyncMock())
    health_close = AsyncMock(side_effect=RuntimeError("health cleanup failed") if health_failure else None)
    monkeypatch.setattr(main, "shutdown_service_health_client", health_close)

    context = main._lifespan(main.app)
    await context.__aenter__()
    client = await helpers._get_httpx_client()
    assert not client.is_closed
    if health_failure:
        with pytest.raises(RuntimeError, match="health cleanup failed"):
            await context.__aexit__(None, None, None)
    else:
        await context.__aexit__(None, None, None)
    assert client.is_closed
    assert helpers._httpx_client is None
    assert helpers._httpx_client_lock is None

    health_close.side_effect = None
    async with main._lifespan(main.app):
        fresh = await helpers._get_httpx_client()
        assert fresh is not client
        assert not fresh.is_closed
    assert fresh.is_closed
