"""Saved filter controls must affect the next real proxy request."""

import asyncio
import json
import sys

import httpx
import pytest

from test_settings_api import API_KEY, api as api


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
def test_saved_global_and_agent_filters_control_forwarded_tool_schemas(api, monkeypatch, streaming):
    forwarded = []
    reply = {"choices": [{"finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    payload = json.dumps(reply)
    if streaming:
        payload = f"data: {payload}\n\ndata: [DONE]\n\n"

    def provider(request):
        forwarded.append(json.loads(request.content))
        return httpx.Response(200, content=payload)

    original = {"model": "fixture-model", "stream": streaming,
                "messages": [{"role": "user", "content": "Keep the current question."}],
                "tools": [{"type": "function", "function": {"name": name, "parameters": {}}}
                          for name in ("first", "second")], "tool_choice": "auto"}
    api.init_db()

    async def exercise():
        async with httpx.AsyncClient(base_url="https://fixture-provider.invalid",
                                     transport=httpx.MockTransport(provider)) as upstream:
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy",
                    headers={"Authorization": f"Bearer {API_KEY}"}) as client:
                async def save(settings):
                    response = await client.post("/api/settings", json=settings)
                    assert response.status_code == 200, response.text

                async def proxy(expected_names):
                    response = await client.post("/v1/chat/completions", json=original)
                    assert response.status_code == 200
                    assert response.text == payload
                    sent = forwarded[-1]
                    assert [tool["function"]["name"] for tool in sent["tools"]] == expected_names
                    assert {key: sent[key] for key in ("model", "stream", "messages", "tool_choice")} == {
                        key: original[key] for key in ("model", "stream", "messages", "tool_choice")}

                await save({"filters": {"enabled": True,
                    "tools": {"enabled": True, "mode": "allowlist", "allowlist": ["first"]}}})
                await proxy(["first"])
                # Null category fields inherit, while one per-agent field overrides.
                # A different agent's disable must not disable the active agent.
                await save({"agents": {
                    "unrelated-agent": {"filters": {"enabled": False}},
                    "local-agent": {"filters": {"enabled": None,
                        "tools": {"enabled": None, "allowlist": ["second"]}}},
                }})
                await proxy(["second"])
                await save({"agents": {"local-agent": {"filters": {"enabled": False}}}})
                await proxy(["first", "second"])
                # Removing the override with null restores the global filter.
                await save({"agents": {"local-agent": {"filters": {
                    "enabled": None, "tools": {"allowlist": None}}}}})
                await proxy(["first"])

    try:
        asyncio.run(exercise())
    finally:
        sys.modules["db"]._get_conn().close()
    assert len(forwarded) == 4
