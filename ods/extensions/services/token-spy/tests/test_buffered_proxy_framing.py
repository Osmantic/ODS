"""Public proxy responses must describe the decoded bytes sent downstream."""
import asyncio
import gzip
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/chat/completions", "/v1/models"])
@pytest.mark.parametrize("status", [200, 429])
def test_compressed_buffered_response(monkeypatch, path, status):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "framing-fixture-key")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-provider-key")
    spec = importlib.util.spec_from_file_location("token_spy_framing", service / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    entries = []
    monkeypatch.setattr(api, "_log_entry", lambda *args, **kwargs: entries.append(args[5]))
    body = {"data": [{"id": "fixture-model"}], "usage": {
        "input_tokens": 12, "output_tokens": 3,
        "prompt_tokens": 12, "completion_tokens": 3}, "choices": []}
    payload = json.dumps(body).encode()
    compressed = gzip.compress(payload)

    async def exercise():
        def respond(request):
            assert "gzip" in request.headers["accept-encoding"]
            return httpx.Response(status, content=compressed, headers={
                "content-type": "application/json", "content-encoding": "gzip",
                "content-length": str(len(compressed)), "x-request-id": "fixture-request"})
        async with httpx.AsyncClient(base_url="https://provider.example",
                transport=httpx.MockTransport(respond)) as upstream:
            monkeypatch.setattr(api, "get_http_client", lambda: upstream)
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer framing-fixture-key"}) as client:
                if path == "/v1/models":
                    response = await client.get(path)
                else:
                    response = await client.post(path, json={
                        "model": "fixture-model", "messages": [], "stream": False})
                assert response.status_code == status
                assert response.content == payload
                assert response.json() == body
                assert "content-encoding" not in response.headers
                assert int(response.headers["content-length"]) == len(payload)
                assert response.headers["x-request-id"] == "fixture-request"
                assert response.headers["content-type"] == "application/json"
    asyncio.run(exercise())
    if path != "/v1/models":
        assert len(entries) == 1
        assert entries[0]["input_tokens"] == 12
        assert entries[0]["output_tokens"] == 3
