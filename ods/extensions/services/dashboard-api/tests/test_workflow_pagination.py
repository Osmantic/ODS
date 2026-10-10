"""Read n8n 2.6.4 cursor inventory through real HTTP and the catalog endpoint."""
import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import workflows
from security import verify_api_key


@pytest.fixture
def n8n_inventory(monkeypatch):
    cursor = base64.b64encode(json.dumps({"limit": 100, "offset": 100}, separators=(",", ":")).encode()).decode()
    state: dict[str, Any] = {"cursor": cursor, "seen": [], "status": 200, "repeat": False, "key": "first-key", "deleted": [], "payloads": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_DELETE(self):
            state["deleted"].append((self.path, self.headers.get("X-N8N-API-KEY")))
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            executions = urlsplit(self.path).path == "/api/v1/executions"
            key = self.headers.get("X-N8N-API-KEY")
            state["seen"].append((query, key))
            second = "cursor" in query
            status = state["status"] if second else 200
            if key != state["key"]:
                status = 401
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = [{"id": "second", "name": "Beta", "active": True}] if second else [
                {"id": "first", "name": "Alpha", "active": True},
                *[{"id": str(index), "name": f"Unrelated {index}"} for index in range(99)],
            ]
            payload = {"data": data, "nextCursor": cursor if not second or state["repeat"] else None}
            if executions:
                payload = {"data": [{"id": "run-1", "workflowId": "second", "finished": True}]}
            page = "executions" if executions else "second" if second else "first"
            payload = state["payloads"].get(page, payload)
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(workflows, "N8N_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(workflows, "read_live_env_value", lambda _key: state["key"])
    async def available():
        return True
    monkeypatch.setattr(workflows, "check_n8n_available", available)
    monkeypatch.setattr(workflows, "load_workflow_catalog", lambda: {"workflows": [
        {"id": name.lower(), "name": name, "description": name} for name in ("Alpha", "Beta")]})
    app = FastAPI()
    app.include_router(workflows.router)
    app.dependency_overrides[verify_api_key] = lambda: "fixture"
    try:
        with TestClient(app) as client:
            yield state, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_catalog_reads_full_inventory_and_live_key_changes(n8n_inventory):
    state, client = n8n_inventory
    for key in ("first-key", "rotated-key"):
        state["key"] = key
        response = client.get("/api/workflows")
        assert response.status_code == 200
        assert [row["n8nId"] for row in response.json()["workflows"]] == ["first", "second"]
        assert state["seen"][-2:] == [({}, key), ({"cursor": [state["cursor"]]}, key)]


@pytest.mark.parametrize("status", [401, 403, 503])
def test_later_page_failure_never_publishes_partial_inventory(n8n_inventory, status, caplog):
    state, client = n8n_inventory
    state["status"] = status
    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert all(row["n8nId"] is None for row in response.json()["workflows"])
    assert state["key"] not in caplog.text
    state["status"] = 200
    assert [row["n8nId"] for row in client.get("/api/workflows").json()["workflows"]] == ["first", "second"]


def test_repeated_cursor_is_refused(n8n_inventory):
    state, client = n8n_inventory
    state["repeat"] = True
    assert client.get("/api/workflows").status_code == 502
    assert len(state["seen"]) == 2


@pytest.mark.parametrize("cursor", [False, 0, [], {}])
def test_falsey_non_string_cursor_is_not_end_of_inventory(n8n_inventory, cursor):
    state, client = n8n_inventory
    state["payloads"]["second"] = {"data": [], "nextCursor": cursor}
    assert client.get("/api/workflows").status_code == 502
    assert len(state["seen"]) == 2


def test_remove_finds_later_page_workflow_and_targets_its_id(n8n_inventory):
    state, client = n8n_inventory
    response = client.delete("/api/workflows/beta")
    assert response.status_code == 200
    assert response.json()["workflowId"] == "beta"
    assert state["deleted"] == [("/api/v1/workflows/second", state["key"])]


@pytest.mark.parametrize("page", ["first", "second"])
@pytest.mark.parametrize("payload", [
    None,
    [],
    {"message": "unexpected upstream response"},
    {"data": None},
    {"data": {"id": "wrong-container"}},
    {"data": ["unexpected-item"]},
])
def test_malformed_page_discards_inventory_and_prevents_removal(n8n_inventory, page, payload, caplog):
    state, client = n8n_inventory
    state["payloads"][page] = payload

    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert all(row["n8nId"] is None for row in response.json()["workflows"])
    assert client.delete("/api/workflows/alpha").status_code == 404
    assert state["deleted"] == []
    assert state["key"] not in caplog.text

    state["payloads"].clear()
    assert [row["n8nId"] for row in client.get("/api/workflows").json()["workflows"]] == ["first", "second"]


@pytest.mark.parametrize("payload", [None, [], {}, {"data": None}, {"data": {}}, {"data": ["bad-item"]}])
def test_malformed_execution_receipt_returns_error_and_recovers(n8n_inventory, payload):
    state, client = n8n_inventory
    state["payloads"]["executions"] = payload
    response = client.get("/api/workflows/beta/executions?limit=3")
    assert response.status_code == 200
    assert response.json() == {"executions": [], "error": "Failed to fetch executions"}

    state["payloads"].clear()
    response = client.get("/api/workflows/beta/executions?limit=3")
    assert response.status_code == 200
    assert response.json() == {
        "workflowId": "beta", "n8nId": "second",
        "executions": [{"id": "run-1", "workflowId": "second", "finished": True}],
    }
    assert state["seen"][-1] == ({"workflowId": ["second"], "limit": ["3"]}, state["key"])


@pytest.mark.asyncio
async def test_inventory_cancellation_propagates_without_partial_result(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(workflows.aiohttp, "ClientSession", lambda **_kwargs: Session())
    with pytest.raises(asyncio.CancelledError):
        await workflows.get_n8n_workflows()


@pytest.mark.asyncio
async def test_total_inventory_deadline_closes_session(monkeypatch):
    closed = []

    class Page:
        async def __aenter__(self):
            await asyncio.Event().wait()

        async def __aexit__(self, *_args):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            closed.append(True)
            return False

        def get(self, *_args, **_kwargs):
            return Page()

    timeout = workflows._async_timeout
    def short_deadline(seconds):
        assert seconds == 5
        return timeout(0.01)
    monkeypatch.setattr(workflows, "_async_timeout", short_deadline)
    monkeypatch.setattr(workflows.aiohttp, "ClientSession", lambda **_kwargs: Session())
    assert await asyncio.wait_for(workflows.get_n8n_workflows(), timeout=0.2) == []
    assert closed == [True]
