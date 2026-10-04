"""Exercise generated Token Spy credentials through collection and HTTP metrics."""

import json
import threading
from typing import TypedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_monitor
from routers import agents
from security import verify_api_key


class TelemetryState(TypedDict):
    key: str
    seen: list[str | None]


@pytest.fixture
def telemetry(monkeypatch, tmp_path):
    state: TelemetryState = {"key": "generated-key", "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["seen"].append(self.headers.get("Authorization"))
            accepted = self.headers.get("Authorization") == f"Bearer {state['key']}"
            self.send_response(200 if accepted else 401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"total_output_tokens": 42}] if accepted else {}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    key_file = tmp_path / "token-spy-api-key.txt"
    key_file.write_text(state["key"], encoding="utf-8")
    monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "")
    monkeypatch.setattr(agent_monitor, "TOKEN_SPY_KEY_FILE", key_file, raising=False)
    monkeypatch.setattr(agent_monitor, "agent_metrics", agent_monitor.AgentMetrics())
    app = FastAPI()
    app.include_router(agents.router)
    app.dependency_overrides[verify_api_key] = lambda: "fixture"
    try:
        with TestClient(app) as client:
            yield state, key_file, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_generated_key_collects_and_rotation_is_read_on_next_poll(telemetry):
    state, key_file, client = telemetry
    for key in ("generated-key", "rotated-key"):
        state["key"] = key
        key_file.write_text(key + "\n", encoding="utf-8")
        await agent_monitor._fetch_token_spy_metrics()
        result = client.get("/api/agents/metrics").json()["agent"]
        assert result["session_count"] == 1
        assert result["output_tokens_24h"] == 42
        assert state["seen"][-1] == f"Bearer {key}"


@pytest.mark.asyncio
async def test_explicit_environment_key_takes_precedence(telemetry, monkeypatch):
    state, _key_file, client = telemetry
    state["key"] = "configured-key"
    monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "configured-key")
    await agent_monitor._fetch_token_spy_metrics()
    assert client.get("/api/agents/metrics").json()["agent"]["output_tokens_24h"] == 42
    assert state["seen"] == ["Bearer configured-key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_file", ["missing", "invalid_utf8"])
async def test_unavailable_key_does_not_fabricate_authenticated_metrics(telemetry, bad_file):
    state, key_file, client = telemetry
    if bad_file == "missing":
        key_file.unlink()
    else:
        key_file.write_bytes(b"\xff")
    await agent_monitor._fetch_token_spy_metrics()
    result = client.get("/api/agents/metrics").json()["agent"]
    assert result["output_tokens_24h"] is None
    assert state["seen"] == [None]
    key_file.write_text(state["key"], encoding="utf-8")
    await agent_monitor._fetch_token_spy_metrics()
    assert client.get("/api/agents/metrics").json()["agent"]["output_tokens_24h"] == 42
    assert state["seen"][-1] == f"Bearer {state['key']}"
