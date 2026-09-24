"""Tool filtering must forward a choice that still names an available tool."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("mode", ["blocklist", "allowlist"])
@pytest.mark.parametrize("choice", ["blocked", "kept", "auto", "required", "none", None, {"function": None}])
def test_filtered_proxy_keeps_tool_choice_consistent(monkeypatch, tmp_path, mode, choice):
    service_dir = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "filter-test-key")
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "settings.json"))
    spec = importlib.util.spec_from_file_location("token_spy_filter_choice", service_dir / "main.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    monkeypatch.setattr(proxy, "get_filter_settings", lambda agent: {
        "enabled": True,
        "tools": {"enabled": True, "mode": mode, "blocklist": ["blocked"], "allowlist": ["kept"]},
    })
    monkeypatch.setattr(proxy, "_log_entry", lambda *args, **kwargs: None)
    forwarded = []

    def upstream(request):
        forwarded.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [], "usage": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://model")
    monkeypatch.setattr(proxy, "get_moonshot_client", lambda: client)
    tool_choice = {"type": "function", "function": {"name": choice}} if isinstance(choice, str) and choice in {"blocked", "kept"} else choice
    response = TestClient(proxy.app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer filter-test-key"},
        json={"model": "local", "messages": [{"role": "user", "content": "Hello"}],
              "tools": [{"type": "function", "function": {"name": name}} for name in ["blocked", "kept"]],
              "tool_choice": tool_choice},
    )
    assert response.status_code == 200
    assert [tool["function"]["name"] for tool in forwarded[0]["tools"]] == ["kept"]
    if choice == "blocked":
        assert "tool_choice" not in forwarded[0]
    else:
        assert forwarded[0]["tool_choice"] == tool_choice
