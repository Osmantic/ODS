"""Cached OpenAI prompt tokens reach storage and reports exactly once."""
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
    spec = importlib.util.spec_from_file_location(f"cache_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def public_events(api):
    """Collect a finite authenticated /token_events ASGI connection."""
    disconnected = asyncio.Event()
    bodies = []
    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        body = message.get("body", b"")
        bodies.append(body)
        if body.startswith(b":heartbeat"):
            disconnected.set()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/token_events", "raw_path": b"/token_events", "query_string": b"",
             "headers": [(b"authorization", b"Bearer cache-fixture-key")],
             "client": ("127.0.0.1", 1), "server": ("fixture", 80)}
    await asyncio.wait_for(api.app(scope, receive, send), timeout=2)
    return [json.loads(body[6:]) for body in bodies if body.startswith(b"data: ")]


def test_public_events_preserve_legacy_and_new_receipt_totals(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "cache-fixture-key")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    db.init_db()
    conn = db._get_conn()
    conn.execute("INSERT INTO usage(agent,model,input_tokens,output_tokens,cache_read_tokens) "
                 "VALUES('legacy','gpt-4o',2048,100,1536)")
    conn.commit()
    db.log_usage({"agent": "new", "model": "gpt-4o", "input_tokens": 512,
                  "output_tokens": 100, "cache_read_tokens": 1536, "cache_write_tokens": 0})
    api = load("main.py")
    try:
        events = asyncio.run(public_events(api))
        assert [event["total_tokens"] for event in events] == [2148, 2148]
    finally:
        conn.close()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prompt,details,cached,total_prompt", [
    (2048, {"cached_tokens": 1536}, 1536, 2048),
    (2048, {"cached_tokens": 2048}, 2048, 2048),
    (2048, None, 0, 2048),
    (2048, {}, 0, 2048),
    (None, {"cached_tokens": 3}, 0, 0),
    (100, {"cached_tokens": 500}, 100, 100),
    (100, {"cached_tokens": -5}, 0, 100),
    (100, "unavailable", 0, 100),
    ("100", {"cached_tokens": "25"}, 25, 100),
])
def test_cached_prompt_receipt_and_report(monkeypatch, tmp_path, streaming, prompt, details, cached, total_prompt):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "cache-fixture-key")
    monkeypatch.setenv("LOCAL_MODEL_AGENTS", "")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    db.init_db()
    api = load("main.py")
    api.SETTINGS_PATH = str(tmp_path / "settings.json")
    monkeypatch.setattr(api, "OPENAI_UPSTREAM", "https://provider.example")
    usage = {"prompt_tokens": prompt, "completion_tokens": 100,
             "prompt_tokens_details": details}
    data = {"choices": [{"finish_reason": "stop"}], "usage": usage}
    payload = json.dumps(data)
    if streaming:
        payload = "data: " + payload + "\n\ndata: [DONE]\n\n"

    async def exercise():
        async with httpx.AsyncClient(base_url="https://provider.example",
                transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as upstream:
            monkeypatch.setattr(api, "get_moonshot_client", lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                    base_url="http://token-spy.test",
                    headers={"Authorization": "Bearer cache-fixture-key"}) as client:
                response = await client.post("/v1/chat/completions", json={
                    "model": "gpt-4o", "stream": streaming,
                    "messages": [{"role": "user", "content": "Hello"}]})
                assert response.status_code == 200
                assert response.text == payload
                today = datetime.now(timezone.utc).date().isoformat()
                report = await client.get("/api/report", params={"start": today, "end": today})
                assert report.status_code == 200
                events = await public_events(api)
                assert len(events) == 1
                assert events[0]["total_tokens"] == total_prompt + 100
                return report.json()

    try:
        report = asyncio.run(exercise())
        summary = report["summary"]
        assert summary["requests"] == 1
        assert summary["input_tokens"] == total_prompt - cached
        assert summary["cache_read_tokens"] == cached
        assert summary["output_tokens"] == 100
        assert summary["total_tokens"] == total_prompt + 100
        expected_cost = ((total_prompt - cached) * 2.50 + cached * 1.25 + 100 * 10) / 1_000_000
        row = db.query_usage()[0]
        assert row["estimated_cost_usd"] == pytest.approx(round(expected_cost, 6))
        from providers.openai import OpenAICompatibleProvider
        adapter = OpenAICompatibleProvider()
        extracted = (adapter.extract_usage_from_stream("data: " + json.dumps(data))
                     if streaming else adapter.extract_usage_from_response(data))
        assert extracted["input_tokens"] == total_prompt - cached
        assert extracted.get("cache_read_tokens", 0) == cached
        assert adapter.calculate_cost(extracted, "gpt-4o") == pytest.approx(expected_cost)
    finally:
        db._local.conn.close()
