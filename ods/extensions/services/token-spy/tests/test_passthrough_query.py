"""Authenticated passthrough preserves SDK pagination and repeated filters."""
import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_passthrough_preserves_encoded_query(monkeypatch, provider):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "query-fixture-key")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-provider-key")
    spec = importlib.util.spec_from_file_location("token_spy_query", service / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "API_PROVIDER", provider)
    query = "limit=1&after_id=model%2Fone%2Btwo&tag=a%20b&tag=c%26d&empty="

    async def exercise():
        seen = []
        def respond(request):
            seen.append(request.url.query)
            if request.url.query == query.encode():
                return httpx.Response(200, json={"data": [{"id": "second-model"}]})
            return httpx.Response(200, json={"data": [{"id": "first-model"}]})
        async with httpx.AsyncClient(base_url="https://provider.example",
                transport=httpx.MockTransport(respond)) as upstream:
            monkeypatch.setattr(api, "get_http_client", lambda: upstream)
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer query-fixture-key"}) as client:
                denied = await client.get("/v1/models?" + query,
                                          headers={"Authorization": "Bearer wrong-key"})
                assert denied.status_code == 403
                assert seen == []
                page = await client.get("/v1/models?" + query)
                assert page.status_code == 200
                assert page.json()["data"] == [{"id": "second-model"}]
                assert seen == [query.encode()]
                first = await client.get("/v1/models")
                assert first.json()["data"] == [{"id": "first-model"}]
                assert seen[-1] == b""
    asyncio.run(exercise())
