"""Tool filtering must leave a request the upstream provider can execute."""
import asyncio
import json

import httpx
import pytest
import test_interrupted_usage_receipts as receipts

proxy = receipts.proxy


@pytest.mark.parametrize("mode", ["blocklist", "allowlist"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("choice", ["blocked", "kept", "auto", "required", "none"])
def test_proxy_reconciles_tool_choice_with_retained_tools(proxy, monkeypatch, mode, stream, choice):
    monkeypatch.setattr(proxy, "get_filter_settings", lambda _: {
        "enabled": True,
        "tools": {"enabled": True, "mode": mode,
                  "blocklist": ["blocked"], "allowlist": ["kept"]},
    })
    tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
             for name in ("blocked", "kept")]
    selected = {"type": "function", "function": {"name": choice}} if choice in {"blocked", "kept"} else choice
    seen = []

    def upstream(request):
        body = json.loads(request.content)
        seen.append(body)
        if stream:
            return httpx.Response(200, text='data: {"choices":[]}\n\ndata: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [], "usage": {}})

    async def exercise():
        async with httpx.AsyncClient(base_url="https://upstream.test",
                                    transport=httpx.MockTransport(upstream)) as provider:
            monkeypatch.setattr(proxy, "get_moonshot_client", lambda: provider)
            async with httpx.AsyncClient(base_url="http://proxy.test",
                    transport=httpx.ASGITransport(app=proxy.app),
                    headers={"Authorization": "Bearer receipt-fixture-key"}) as client:
                response = await client.post("/v1/chat/completions", json={
                    "model": "test-model", "messages": [{"role": "user", "content": "Hello"}],
                    "tools": tools, "tool_choice": selected, "stream": stream,
                })
                assert response.status_code == 200
                assert len(seen) == 1
                forwarded = seen[0]
                assert [tool["function"]["name"] for tool in forwarded["tools"]] == ["kept"]
                if choice == "blocked":
                    assert "tool_choice" not in forwarded
                else:
                    assert forwarded["tool_choice"] == selected
                if stream:
                    assert response.text.endswith("data: [DONE]\n\n")
    asyncio.run(exercise())
