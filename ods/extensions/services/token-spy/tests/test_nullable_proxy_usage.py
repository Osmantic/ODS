"""Optional upstream telemetry must not interrupt response passthrough."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
def test_null_usage_does_not_interrupt_proxy(monkeypatch, protocol, stream):
    service_dir = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "nullable-usage-test")
    monkeypatch.setenv("UPSTREAM_API_KEY", "upstream-test-key")
    spec = importlib.util.spec_from_file_location("token_spy_nullable_usage", service_dir / "main.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    monkeypatch.setattr(proxy, "get_filter_settings", lambda agent: {})
    entries = []
    monkeypatch.setattr(proxy, "_log_entry", lambda *args, **kwargs: entries.append(dict(args[5])))
    path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
    if stream and protocol == "openai":
        payload = 'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,"prompt_tokens_details":null}}\n\ndata: [DONE]\n\n'
    elif stream:
        payload = 'event: message_start\ndata: {"message":{"usage":null}}\n\nevent: message_delta\ndata: {"usage":null,"delta":{"stop_reason":"end_turn"}}\n\nevent: message_stop\ndata: {}\n\n'
    else:
        payload = json.dumps({"choices": [], "usage": None})
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload)),
        base_url="http://upstream",
    )
    monkeypatch.setattr(proxy, "get_moonshot_client", lambda: client)
    monkeypatch.setattr(proxy, "get_http_client", lambda: client)
    response = TestClient(proxy.app).post(
        path, headers={"Authorization": "Bearer nullable-usage-test"},
        json={"model": "test", "messages": [], "stream": stream},
    )
    assert response.status_code == 200
    assert response.text == payload
    assert len(entries) == 1
    assert entries[0]["input_tokens"] == (7 if stream and protocol == "openai" else 0)
    assert entries[0]["cache_read_tokens"] == 0
