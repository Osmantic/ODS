"""Route regressions: catalog actions must never target a partial name match."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@contextmanager
def n8n_server(rows):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET", self.path))
            payload = {"data": rows} if self.path.startswith("/api/v1/workflows") else {"data": [{"id": "execution"}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_DELETE(self):
            calls.append(("DELETE", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def catalog(monkeypatch):
    import routers.workflows as workflows

    # Use the installed producer's actual catalog and template name, rather
    # than inventing an alias that the enable/import route never produces.
    source = Path(__file__).resolve().parents[4] / "config/n8n"
    data = json.loads((source / "catalog.json").read_text())
    entry = next(row for row in data["workflows"] if row["id"] == "daily-digest")
    template = json.loads((source / entry["file"]).read_text())
    assert entry["name"] == template["name"] == "Daily Digest"
    monkeypatch.setattr(workflows, "load_workflow_catalog", lambda: {"workflows": [entry], "categories": {}})
    monkeypatch.setattr(workflows, "check_workflow_dependencies", AsyncMock(return_value={}))
    monkeypatch.setattr(workflows, "check_n8n_available", AsyncMock(return_value=True))
    return workflows


@pytest.mark.parametrize("name", ["Daily Digest Backup", "Daily", ""])
def test_catalog_does_not_adopt_partial_workflow(test_client, catalog, monkeypatch, name):
    with n8n_server([{"id": "foreign", "name": name, "active": True}]) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        response = test_client.get("/api/workflows", headers=test_client.auth_headers)
    assert response.status_code == 200
    workflow = response.json()["workflows"][0]
    assert workflow["status"] == "available"
    assert workflow["installed"] is False
    assert workflow["n8nId"] is None
    assert calls == [("GET", "/api/v1/workflows")]


@pytest.mark.parametrize("method,path", [("post", "/api/workflows/daily-digest/disable"), ("delete", "/api/workflows/daily-digest")])
def test_remove_never_deletes_similarly_named_owner_workflow(test_client, catalog, monkeypatch, method, path):
    with n8n_server([{"id": "foreign", "name": "Daily Digest Backup", "active": True}]) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        response = getattr(test_client, method)(path, headers=test_client.auth_headers)
    assert response.status_code == 404
    assert not any(method == "DELETE" for method, _ in calls)


def test_executions_do_not_read_partial_match(test_client, catalog, monkeypatch):
    with n8n_server([{"id": "foreign", "name": "Daily Digest Backup", "active": True}]) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        response = test_client.get("/api/workflows/daily-digest/executions", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["executions"] == []
    assert calls == [("GET", "/api/v1/workflows")]


@pytest.mark.parametrize("operation", ["catalog", "disable", "delete", "executions"])
def test_exact_match_wins_regardless_of_inventory_order(test_client, catalog, monkeypatch, operation):
    rows = [{"id": "foreign", "name": "Daily Digest Backup", "active": True},
            {"id": "selected", "name": "daily digest", "active": False}]
    with n8n_server(rows) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        if operation == "catalog":
            response = test_client.get("/api/workflows", headers=test_client.auth_headers)
            assert response.json()["workflows"][0]["n8nId"] == "selected"
            assert response.json()["workflows"][0]["status"] == "installed"
        elif operation == "executions":
            response = test_client.get("/api/workflows/daily-digest/executions", headers=test_client.auth_headers)
            assert response.json()["n8nId"] == "selected"
            assert calls[-1] == ("GET", "/api/v1/executions?workflowId=selected&limit=20")
        else:
            response = (test_client.post("/api/workflows/daily-digest/disable", headers=test_client.auth_headers)
                        if operation == "disable" else test_client.delete("/api/workflows/daily-digest", headers=test_client.auth_headers))
            assert calls[-1] == ("DELETE", "/api/v1/workflows/selected")
    assert response.status_code == 200


@pytest.mark.parametrize("method,path", [("post", "/api/workflows/daily-digest/disable"),
                                        ("delete", "/api/workflows/daily-digest"), ("get", "/api/workflows/daily-digest/executions")])
def test_duplicate_exact_names_require_operator_selection(test_client, catalog, monkeypatch, method, path):
    rows = [{"id": "first", "name": "Daily Digest", "active": True},
            {"id": "second", "name": "Daily Digest", "active": False}]
    with n8n_server(rows) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        response = getattr(test_client, method)(path, headers=test_client.auth_headers)
    assert response.status_code == 409
    assert "n8n" in response.json()["detail"]
    assert calls == [("GET", "/api/v1/workflows")]


@pytest.mark.parametrize("ambiguous_first", [True, False])
def test_duplicate_names_do_not_hide_unrelated_catalog_entry(test_client, catalog, monkeypatch, ambiguous_first):
    source = Path(__file__).resolve().parents[4] / "config/n8n/catalog.json"
    data = json.loads(source.read_text())
    entries = [next(row for row in data["workflows"] if row["id"] == workflow_id)
               for workflow_id in ("daily-digest", "code-assistant")]
    if not ambiguous_first:
        entries.reverse()
    monkeypatch.setattr(catalog, "load_workflow_catalog", lambda: {"workflows": entries, "categories": data["categories"]})
    rows = [{"id": "first", "name": "Daily Digest", "active": True},
            {"id": "second", "name": "DAILY DIGEST", "active": False},
            {"id": "unrelated", "name": "Code Assistant", "active": True,
             "statistics": {"executions": {"total": 7}}}]

    with n8n_server(rows) as (url, calls):
        monkeypatch.setattr(catalog, "N8N_URL", url)
        response = test_client.get("/api/workflows", headers=test_client.auth_headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["categories"] == data["categories"]
        assert payload["n8nAvailable"] is True
        assert [workflow["id"] for workflow in payload["workflows"]] == [entry["id"] for entry in entries]
        by_id = {workflow["id"]: workflow for workflow in payload["workflows"]}

        ambiguous = by_id["daily-digest"]
        assert ambiguous["status"] == "ambiguous"
        assert "duplicate names in n8n" in ambiguous["error"]
        assert ambiguous["n8nId"] is None
        assert ambiguous["installed"] is False
        assert ambiguous["active"] is False
        assert ambiguous["executions"] == 0

        unrelated = by_id["code-assistant"]
        assert unrelated["status"] == "active"
        assert unrelated["n8nId"] == "unrelated"
        assert unrelated["installed"] is True
        assert unrelated["active"] is True
        assert unrelated["executions"] == 7
        assert "error" not in unrelated

        for method, path in (("post", "/api/workflows/daily-digest/disable"),
                             ("delete", "/api/workflows/daily-digest")):
            refused = getattr(test_client, method)(path, headers=test_client.auth_headers)
            assert refused.status_code == 409
            assert "n8n" in refused.json()["detail"]

    assert calls == [("GET", "/api/v1/workflows")] * 3
