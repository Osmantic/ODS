"""Actual dashboard transport -> host HTTP -> private storage, not installed acceptance."""
from contextlib import asynccontextmanager
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import host_agent_client as transport
from routers import pixel_settings
import security


@pytest.fixture
def actual_stack(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[4] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("_settings_proxy_actual_host", path)
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)
    agent.DATA_DIR = tmp_path
    agent.AGENT_API_KEY = "synthetic-settings-host-key"

    class Handler(agent.AgentHandler):
        posts = 0

        def do_POST(self):
            Handler.posts += 1
            return super().do_POST()

    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    monkeypatch.setattr(transport, "AGENT_URL", "http://127.0.0.1:" + str(listener.server_port))
    monkeypatch.setattr(transport, "ODS_AGENT_KEY", agent.AGENT_API_KEY)
    monkeypatch.setattr(transport, "_sync_client", None)
    monkeypatch.setattr(transport, "_async_client", None)
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "synthetic-settings-dashboard-key")

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await transport.shutdown_clients()

    app = FastAPI(lifespan=lifespan)
    app.include_router(pixel_settings.router)
    thread.start()
    try:
        with TestClient(app, headers={"Authorization": "Bearer synthetic-settings-dashboard-key"}) as client:
            yield client, agent, Handler
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
        sys.modules.pop(spec.name, None)


def test_actual_proxy_save_reset_reload_and_conflict(actual_stack, tmp_path):
    client, _agent, handler = actual_stack
    initial = client.get("/api/pixel/settings")
    assert initial.status_code == 200
    assert initial.json()["configuration"]["revision"] == 0
    assert list(tmp_path.iterdir()) == []
    saved = client.post("/api/pixel/settings/save", json={"expectedRevision": 0,
        "changes": {"contextTokens": 65536, "temperature": 1, "compactionNotify": True}})
    assert saved.status_code == 200, saved.text
    reset = client.post("/api/pixel/settings/save", json={"expectedRevision": 1,
        "changes": {"contextTokens": None}})
    assert reset.status_code == 200, reset.text
    assert reset.json()["configuration"]["preferences"] == {
        "contextTokens": None, "temperature": 1, "compactionNotify": True}
    assert reset.json()["runtime"]["status"] == "not-inspected"
    assert client.get("/api/pixel/settings").json() == reset.json()
    assert client.post("/api/pixel/settings/save", json={"expectedRevision": 1, "changes": {}}).status_code == 409
    assert handler.posts == 3
    assert json.loads((tmp_path / "pixel-providers/pixel-settings.json").read_bytes()) == reset.json()["configuration"]
    assert not (tmp_path / "pixel-providers/provider-config.json").exists()


def test_invalid_changes_never_reach_actual_host(actual_stack, tmp_path):
    client, _agent, handler = actual_stack
    result = client.post("/api/pixel/settings/save", json={"expectedRevision": 0,
        "changes": {"apiKey": "private-sentinel"}})
    assert result.status_code == 400
    assert "private-sentinel" not in result.text
    assert handler.posts == 0
    assert list(tmp_path.iterdir()) == []


def test_lost_success_response_is_not_retried_and_reload_recovers(actual_stack, monkeypatch):
    client, agent, handler = actual_stack
    original_response = agent.json_response

    def drop_committed_response(request, status, value, **options):
        if request.command == "POST" and status == 200:
            request.close_connection = True
            request.connection.shutdown(socket.SHUT_RDWR)
            request.connection.close()
            return
        return original_response(request, status, value, **options)

    monkeypatch.setattr(agent, "json_response", drop_committed_response)
    result = client.post("/api/pixel/settings/save", json={"expectedRevision": 0,
        "changes": {"verbosity": "full"}})
    assert result.status_code == 503
    assert handler.posts == 1
    reloaded = client.get("/api/pixel/settings")
    assert reloaded.status_code == 200
    assert reloaded.json()["configuration"] == {
        "schemaVersion": 1, "revision": 1, "preferences": {"verbosity": "full"}}


@pytest.fixture
def controller_stub(actual_stack, monkeypatch):
    """Real dashboard/host HTTP, explicitly simulated privileged controller."""
    import pixel_access_client
    from pixel_settings import host_api
    monkeypatch.setattr(host_api.platform, "system", lambda: "Linux")
    calls = []
    state = {"status": "pending", "revision": "a" * 64, "settingsRevision": 3,
             "appliedRevision": None, "pending": True, "capabilities": None, "lastVerifiedAt": None}

    def request(operation, body=None, **kwargs):
        calls.append((operation, body, kwargs))
        if operation == "settings-status": return 200, dict(state)
        return 200, {"outcome": "rolled-back", "appliedRevision": None}

    monkeypatch.setattr(pixel_access_client, "request_access", request)
    return calls, state


def test_runtime_inspection_and_recovery_use_actual_host_path(actual_stack, controller_stub, tmp_path):
    client, _agent, handler = actual_stack
    calls, _state = controller_stub
    # Recovery remains inspectable even if the preferences file cannot be loaded.
    assert client.post("/api/pixel/settings/save", json={"expectedRevision": 0, "changes": {}}).status_code == 200
    (tmp_path / "pixel-providers/pixel-settings.json").write_bytes(b"corrupt")
    assert client.get("/api/pixel/settings").status_code == 503
    result = client.get("/api/pixel/settings/runtime")
    assert result.status_code == 200, result.text
    assert result.json()["pending"] is True
    assert result.headers["cache-control"] == "no-store"
    payload = {"operation": "recover", "revision": result.json()["revision"], "settingsRevision": 3}
    outcome = client.post("/api/pixel/settings/runtime", json=payload)
    assert outcome.status_code == 200, outcome.text
    assert outcome.json() == {"outcome": "rolled-back", "appliedRevision": None}
    assert calls == [("settings-status", None, {"settings_data_dir": tmp_path}),
                     ("settings-change", payload, {"settings_data_dir": tmp_path})]
    assert handler.posts == 2


@pytest.mark.parametrize("extra", [{"path": "/etc"}, {"data_dir_id": "b" * 64}, {"capabilities": {}}])
def test_runtime_rejects_public_control_target_before_actual_host(actual_stack, controller_stub, extra):
    client, _agent, handler = actual_stack
    calls, _state = controller_stub
    response = client.post("/api/pixel/settings/runtime", json={"operation": "apply", "revision": "a" * 64,
                                                              "settingsRevision": 3, **extra})
    assert response.status_code == 400
    assert handler.posts == 0 and calls == []


def test_runtime_lifecycle_conflict_does_not_call_controller(actual_stack, controller_stub):
    client, agent, _handler = actual_stack
    calls, _state = controller_stub
    acquired, _active = agent._begin_model_lifecycle("model_switch")
    assert acquired
    try:
        response = client.post("/api/pixel/settings/runtime", json={"operation": "apply", "revision": "a" * 64, "settingsRevision": 3})
        assert response.status_code == 409
        assert calls == []
    finally:
        agent._end_model_lifecycle("model_switch")


@pytest.mark.parametrize("failure", ["lost", "wrong-revision", "private-error"])
def test_runtime_uncertainty_no_retry_no_secret_and_lifecycle_released(actual_stack, controller_stub, monkeypatch, failure):
    import pixel_access_client
    client, agent, handler = actual_stack
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        if failure == "lost": raise OSError("private-sentinel/full/path")
        if failure == "wrong-revision": return 200, {"outcome": "applied", "appliedRevision": 4}
        return 503, {"error": "private-sentinel/full/path"}
    monkeypatch.setattr(pixel_access_client, "request_access", fail)
    result = client.post("/api/pixel/settings/runtime", json={"operation": "apply", "revision": "a" * 64, "settingsRevision": 3})
    assert result.status_code == 503
    assert "private-sentinel" not in result.text
    assert len(calls) == 1 and handler.posts == 1
    acquired, _active = agent._begin_model_lifecycle("model_switch")
    assert acquired
    agent._end_model_lifecycle("model_switch")


def test_missing_root_controller_leaves_save_usable(actual_stack, controller_stub, monkeypatch):
    import pixel_access_client
    client, _agent, _handler = actual_stack
    def missing(*_args, **_kwargs): raise FileNotFoundError("private-controller-path")
    monkeypatch.setattr(pixel_access_client, "request_access", missing)
    result = client.get("/api/pixel/settings/runtime")
    assert result.status_code == 200
    assert result.json()["status"] == "unavailable" and result.json()["pending"] is None
    assert "private-controller-path" not in result.text
    saved = client.post("/api/pixel/settings/save", json={"expectedRevision": 0, "changes": {"verbosity": "full"}})
    assert saved.status_code == 200
