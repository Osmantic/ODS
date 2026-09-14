"""Unit test suite for LemonadeClient HTTP transport resilience and Bearer auth."""

import httpx
import pytest

from lemonade_client import LemonadeClient, LemonadeSettings, LemonadeClientError


@pytest.mark.asyncio
async def test_lemonade_client_health_check_bearer_auth():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305", api_key="test-secret-key"),
        client=client,
    )

    payload = await adapter.health()
    assert payload["status"] == "ok"
    assert seen["authorization"] == "Bearer test-secret-key"
    await client.aclose()


@pytest.mark.asyncio
async def test_lemonade_client_http_500_raises_lemonade_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305", api_key="key"),
        client=client,
    )

    with pytest.raises(LemonadeClientError):
        await adapter.health()
    await client.aclose()
