"""Exercise cumulative Anthropic usage at Token Spy's authenticated HTTP route."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy(monkeypatch):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "usage-regression-key")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-provider-key")
    spec = importlib.util.spec_from_file_location("token_spy_cumulative_usage", service / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = []
    monkeypatch.setattr(module, "_log_entry", lambda *args, **kwargs: entries.append(dict(args[5])))
    return module, entries


def event(kind, **data):
    return f"event: {kind}\ndata: {json.dumps(dict(type=kind, **data))}\n\n"


@pytest.mark.parametrize("final,expected", [
    ({"input_tokens": 40, "output_tokens": 12, "cache_read_input_tokens": 80,
      "cache_creation_input_tokens": 20}, (40, 12, 80, 20)),
    ({"input_tokens": 0, "output_tokens": 12, "cache_read_input_tokens": 0,
      "cache_creation_input_tokens": 0}, (0, 12, 0, 0)),
    ({"input_tokens": None, "output_tokens": 12}, (10, 12, 30, 5)),
    ({"output_tokens": 12}, (10, 12, 30, 5)),
])
def test_final_cumulative_counters_replace_initial_values(proxy, monkeypatch, final, expected):
    module, entries = proxy
    payload = (
        event("message_start", message={"usage": {"input_tokens": 10, "output_tokens": 1,
              "cache_read_input_tokens": 30, "cache_creation_input_tokens": 5}})
        + event("message_delta", delta={}, usage={"output_tokens": 4})
        + event("message_delta", delta={"stop_reason": "end_turn"}, usage=final)
        + event("message_stop")
    )
    upstream = httpx.AsyncClient(base_url="https://upstream.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)))
    monkeypatch.setattr(module, "get_http_client", lambda: upstream)
    client = TestClient(module.app)
    try:
        response = client.post("/v1/messages",
            headers={"Authorization": "Bearer usage-regression-key"},
            json={"model": "test", "stream": True, "messages": [
                {"role": "user", "content": "Hello"}]})
        assert response.status_code == 200
        assert response.text == payload
        assert len(entries) == 1
        assert tuple(entries[0][key] for key in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
        )) == expected
        assert entries[0]["stop_reason"] == "end_turn"

        from providers.anthropic import AnthropicProvider
        adapter = AnthropicProvider()
        captured = {"input_tokens": 10, "output_tokens": 1,
                    "cache_read_tokens": 30, "cache_write_tokens": 5}
        captured.update(adapter.extract_usage_from_stream(
            "data: " + json.dumps({"usage": final, "delta": {}}), "message_delta") or {})
        assert tuple(captured[key] for key in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
        )) == expected
    finally:
        client.close()
        asyncio.run(upstream.aclose())
