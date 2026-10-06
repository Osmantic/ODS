"""Library selection changes reach the Dashboard resource/restart boundary."""

import asyncio
import importlib.util
import json
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest

import config
import host_agent_client
from main import _cache, app
from routers import resources


@pytest.fixture
def library_runtime(tmp_path, monkeypatch, shipped_agent):
    shipped = Path(__file__).resolve().parents[2]
    root = tmp_path / "services"
    root.mkdir()
    for service_id in config.LIBRARY_MANAGEABLE_BUILTINS:
        target = root / service_id
        target.mkdir()
        shutil.copyfile(shipped / service_id / "manifest.yaml", target / "manifest.yaml")
        compose = shipped / service_id / "compose.yaml"
        if not compose.exists():
            compose = shipped / service_id / "compose.yaml.disabled"
        shutil.copyfile(compose, target / "compose.yaml.disabled")
    monkeypatch.setattr(resources, "SERVICES", {})  # API started with a lean selection.
    monkeypatch.setattr(config, "EXTENSIONS_DIR", root)
    # The candidate imports the same directory constant as existing health reads.
    monkeypatch.setattr(resources, "EXTENSIONS_DIR", root, raising=False)
    monkeypatch.setattr(resources, "DATA_DIR", str(tmp_path / "data"))
    calls = []
    failure = {"status": 200, "docker_calls": [], "observed": ["foreign"]}
    monkeypatch.setattr(shipped_agent, "EXTENSIONS_DIR", root)
    monkeypatch.setattr(shipped_agent, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(shipped_agent, "AGENT_API_KEY", "resource-agent-fixture")

    def docker(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            service_id = command[3].rsplit("=", 1)[-1]
            return subprocess.CompletedProcess(command, 0, stdout=f"ods-{service_id}\n", stderr="")
        assert command[:2] == ["docker", "restart"]
        failure["docker_calls"].append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(shipped_agent, "subprocess", SimpleNamespace(run=docker, TimeoutExpired=subprocess.TimeoutExpired))

    class Agent(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.path == "/v1/service/stats"
            assert self.headers["Authorization"] == "Bearer resource-agent-fixture"
            self.respond({"containers": [{"container_name": f"ods-{sid}", "service_id": sid,
                                          "cpu_percent": 1, "memory_used_mb": 8}
                                         for sid in failure["observed"]]})

        def do_POST(self):
            assert self.path == "/v1/service/restart"
            assert self.headers["Authorization"] == "Bearer resource-agent-fixture"
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(raw)
            calls.append(payload)
            if failure["status"] != 200:
                self.respond({"error": "restart refused"}, failure["status"])
                return
            # Execute the shipped host restart/auth/manifest/lock logic over
            # real HTTP; only Docker process execution is unavailable here.
            from io import BytesIO
            self.rfile = BytesIO(raw)
            shipped_agent.AgentHandler._handle_service_restart(self)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(host_agent_client, "ODS_AGENT_KEY", "resource-agent-fixture")
    monkeypatch.setattr(host_agent_client, "AGENT_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(host_agent_client, "_sync_client", None)
    for key in ("service_resources_containers", "service_resources_disk"):
        _cache.invalidate(key)
    yield root, calls, failure
    if host_agent_client._sync_client is not None:
        host_agent_client._sync_client.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    for key in ("service_resources_containers", "service_resources_disk"):
        _cache.invalidate(key)


@pytest.fixture(scope="module")
def shipped_agent():
    path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("library_resource_host_agent", path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    return agent


def snapshot(client):
    response = client.get("/api/services/resources", headers=client.auth_headers)
    assert response.status_code == 200
    return {row["id"]: row for row in response.json()["services"]}


@pytest.mark.parametrize("service_id", sorted(config.LIBRARY_MANAGEABLE_BUILTINS))
def test_add_back_disable_and_reenable_without_api_restart(test_client, library_runtime, service_id):
    root, calls, failure = library_runtime
    directory = root / service_id
    disabled = directory / "compose.yaml.disabled"
    enabled = directory / "compose.yaml"
    url = f"/api/services/{service_id}/restart"
    assert service_id not in snapshot(test_client)
    assert test_client.post(url, headers=test_client.auth_headers).status_code == 404
    # Library selection commits this same marker move before starting the service.
    disabled.rename(enabled)
    row = snapshot(test_client)[service_id]
    assert row["name"] == config.load_extension_manifests(root, config.GPU_BACKEND)[0][service_id]["name"]
    assert row["restartable"] is True
    assert test_client.post(url, headers=test_client.auth_headers).status_code == 200
    assert calls == [{"service_id": service_id}]
    enabled.rename(disabled)
    assert service_id not in snapshot(test_client)
    assert test_client.post(url, headers=test_client.auth_headers).status_code == 404
    assert len(calls) == 1
    disabled.rename(enabled)
    assert snapshot(test_client)[service_id]["restartable"] is True
    assert test_client.post(url, headers=test_client.auth_headers).status_code == 200
    assert calls == [{"service_id": service_id}] * 2
    assert failure["docker_calls"] == [["docker", "restart", f"ods-{service_id}"]] * 2


def test_retained_import_time_entry_loses_restart_authority_when_disabled(test_client, library_runtime, monkeypatch):
    root, calls, _ = library_runtime
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    startup = config.load_extension_manifests(root, config.GPU_BACKEND)[0]
    monkeypatch.setattr(resources, "SERVICES", startup)
    assert snapshot(test_client)["n8n"]["restartable"] is True
    (directory / "compose.yaml").rename(directory / "compose.yaml.disabled")
    assert "n8n" not in snapshot(test_client)
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 404
    assert calls == []
    assert "n8n" in startup  # Refresh never mutates the import-time registry.


def test_unknown_measured_container_remains_visible_without_restart_authority(test_client, library_runtime):
    _, calls, _ = library_runtime
    assert snapshot(test_client)["foreign"]["restartable"] is False
    assert test_client.post("/api/services/foreign/restart", headers=test_client.auth_headers).status_code == 404
    assert calls == []


def test_cached_container_measurement_never_keeps_disabled_restart_authority(test_client, library_runtime):
    root, calls, failure = library_runtime
    failure["observed"] = ["n8n"]
    assert snapshot(test_client)["n8n"]["restartable"] is False
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    row = snapshot(test_client)["n8n"]
    assert row["restartable"] is True
    assert row["name"] == "n8n (Workflows)"
    assert row["container"]["cpu_percent"] == 1
    (directory / "compose.yaml").rename(directory / "compose.yaml.disabled")
    row = snapshot(test_client)["n8n"]
    assert row["restartable"] is False
    assert row["container"]["cpu_percent"] == 1  # Existing cached measurements remain visible.
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 404
    assert calls == []


@pytest.mark.parametrize("status", [403, 503])
def test_host_refusal_does_not_claim_restart_and_later_request_recovers(test_client, library_runtime, status):
    root, calls, failure = library_runtime
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    failure["status"] = status
    url = "/api/services/n8n/restart"
    assert test_client.post(url, headers=test_client.auth_headers).status_code == status
    failure["status"] = 200
    assert test_client.post(url, headers=test_client.auth_headers).json()["action"] == "restart"
    assert calls == [{"service_id": "n8n"}] * 2


def test_unreadable_selection_fails_closed_then_recovers(test_client, library_runtime, monkeypatch):
    root, calls, _ = library_runtime
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    monkeypatch.setattr(resources, "SERVICES", config.load_extension_manifests(root, config.GPU_BACKEND)[0])
    loader = config.load_extension_manifests

    def failed_read(*args, **kwargs):
        raise OSError("selection mount is unavailable")

    monkeypatch.setattr(resources, "load_extension_manifests", failed_read, raising=False)
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 503
    assert test_client.get("/api/services/resources", headers=test_client.auth_headers).status_code == 503
    assert calls == []
    monkeypatch.setattr(resources, "load_extension_manifests", loader)
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 200


def test_invalid_current_manifest_cannot_use_stale_restart_authority(test_client, library_runtime, monkeypatch):
    root, calls, _ = library_runtime
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    monkeypatch.setattr(resources, "SERVICES", config.load_extension_manifests(root, config.GPU_BACKEND)[0])
    manifest = directory / "manifest.yaml"
    original = manifest.read_bytes()
    manifest.write_text("schema_version: broken\n", encoding="utf-8")
    assert "n8n" not in snapshot(test_client)
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 404
    assert calls == []
    manifest.write_bytes(original)
    assert test_client.post("/api/services/n8n/restart", headers=test_client.auth_headers).status_code == 200


def test_cancelled_selection_read_never_sends_restart_and_next_request_recovers(test_client, library_runtime, monkeypatch):
    root, calls, _ = library_runtime
    directory = root / "n8n"
    (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    entered, release, finished = Event(), Event(), Event()
    loader = config.load_extension_manifests

    def delayed_read(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(5), "test must release selection read"
        try:
            return loader(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(resources, "load_extension_manifests", delayed_read, raising=False)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            request = asyncio.create_task(client.post("/api/services/n8n/restart", headers=test_client.auth_headers))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            finally:
                release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            assert calls == []
            response = await client.post("/api/services/n8n/restart", headers=test_client.auth_headers)
            assert response.status_code == 200

    asyncio.run(scenario())
    assert calls == [{"service_id": "n8n"}]
