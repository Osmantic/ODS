"""Authenticated proxy-to-SQLite receipts must price the actual upstream."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]


def load(filename):
    spec = importlib.util.spec_from_file_location(f"pricing_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "pricing-fixture-key")
    monkeypatch.setenv("UPSTREAM_API_KEY", "synthetic-upstream-key")
    monkeypatch.setenv("LOCAL_MODEL_AGENTS", "")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    db.init_db()
    api = load("main.py")
    api.SETTINGS_PATH = str(tmp_path / "settings.json")
    yield api, db
    # Requests run in one asyncio loop on this thread.
    db._local.conn.close()


def response_body(protocol, streaming):
    if protocol == "openai":
        data = {"choices": [{"finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        return ("data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n"
                if streaming else json.dumps(data))
    data = {"usage": {"input_tokens": 100, "output_tokens": 10},
            "stop_reason": "end_turn"}
    if not streaming:
        return json.dumps(data)
    return (
        'event: message_start\ndata: {"message":{"usage":{"input_tokens":100}}}\n\n'
        'event: message_delta\ndata: {"usage":{"output_tokens":10},"delta":{"stop_reason":"end_turn"}}\n\n'
        'event: message_stop\ndata: {}\n\n'
    )


def receipt(proxy, monkeypatch, protocol, streaming, upstream_url, model):
    api, db = proxy
    path = "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions"
    setting = "ANTHROPIC_UPSTREAM" if protocol == "anthropic" else "OPENAI_UPSTREAM"
    getter = "get_http_client" if protocol == "anthropic" else "get_moonshot_client"
    monkeypatch.setattr(api, setting, upstream_url)
    payload = response_body(protocol, streaming)

    async def exercise():
        def respond(request):
            assert str(request.url) == upstream_url + path
            assert json.loads(request.content)["model"] == model
            return httpx.Response(200, content=payload, headers={
                "content-type": "text/event-stream" if streaming else "application/json"})
        async with httpx.AsyncClient(base_url=upstream_url,
                transport=httpx.MockTransport(respond)) as upstream:
            monkeypatch.setattr(api, getter, lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer pricing-fixture-key"}) as client:
                response = await client.post(path, json={
                    "model": model, "stream": streaming,
                    "messages": [{"role": "user", "content": "Hello"}]})
                assert response.status_code == 200, response.text
                assert response.text == payload
                today = datetime.now(timezone.utc).date().isoformat()
                report = await client.get("/api/report", params={"start": today, "end": today})
                assert report.status_code == 200, report.text
                return report.json()
    report = asyncio.run(exercise())
    assert report["summary"]["requests"] == 1
    assert report["summary"]["input_tokens"] == 100
    assert report["summary"]["output_tokens"] == 10
    return report


@pytest.mark.parametrize("protocol,model", [
    ("openai", "deepseek-reasoner"), ("anthropic", "claude-sonnet-4"),
])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("local", [False, True])
def test_protocol_receipts_use_endpoint_locality(proxy, monkeypatch, protocol, model, streaming, local):
    url = "http://127.0.0.1:8080" if local else "https://provider.example"
    monkeypatch.setattr(proxy[0], "UPSTREAM_BASE_URL", "https://provider.example")
    report = receipt(proxy, monkeypatch, protocol, streaming, url, model)
    row = report["models"][0]
    if local:
        assert row["cost_usd"] == 0
        assert row["provider"] == "local"
        assert row["cost_source"] == "local_zero_cost"
        assert report["summary"]["local_providers"] == 1
        assert report["summary"]["billing_providers"] == 0
    else:
        assert row["cost_usd"] > 0
        assert row["provider"] == protocol
        assert row["cost_source"] == "priced_from_tokens"
        assert report["summary"]["billing_providers"] == 1
        assert report["summary"]["local_providers"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_explicit_remote_upstream_does_not_inherit_local_base(proxy, monkeypatch, streaming):
    monkeypatch.setattr(proxy[0], "UPSTREAM_BASE_URL", "http://localhost:8080")
    report = receipt(proxy, monkeypatch, "openai", streaming,
                     "https://provider.example", "unpriced-fixture-model")
    assert report["models"][0]["provider"] == "openai"
    assert report["models"][0]["cost_source"] == "untracked"
    assert report["summary"]["untracked_providers"] == 1
    assert report["summary"]["local_providers"] == 0
