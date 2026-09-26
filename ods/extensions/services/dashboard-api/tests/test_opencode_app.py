"""OpenCode as a dashboard application: health mapping, app API, and library."""

import json
from unittest.mock import AsyncMock, patch

import pytest

import helpers
from helpers import check_service_health, get_cached_services, set_services_cache
from host_agent_client import AgentHTTPError, AgentUnavailable
from models import ServiceStatus

OPENCODE_CONFIG = {
    "name": "OpenCode (IDE)",
    "port": 3003,
    "external_port": 3003,
    "health": "/",
    "host": "localhost",
    "type": "host-systemd",
    "category": "optional",
    "public_url": "",
}


def lifecycle(state, **extra):
    return {
        "state": state,
        "platform": "linux",
        "port": 3003,
        "installed": state not in ("not_installed", "installing"),
        "registered": state not in ("not_installed", "installing"),
        "healthy": state == "running",
        "reachable": state == "running",
        "portInUse": False,
        "version": "1.18.32" if state == "running" else None,
        "responseTimeMs": 1.5,
        "startSupported": state not in ("not_installed", "installing"),
        "setupSupported": state == "not_installed",
        "setupIssue": None,
        **extra,
    }


@pytest.fixture(autouse=True)
def _reset_lifecycle(monkeypatch):
    monkeypatch.setattr(helpers, "_opencode_lifecycle", None)
    monkeypatch.setattr(helpers, "_services_cache", None)


# --- service health mapping ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [
    ("running", "healthy"),
    ("starting", "degraded"),
    ("installing", "degraded"),
    ("stopped", "down"),
    ("not_installed", "not_deployed"),
])
async def test_lifecycle_maps_onto_service_status(monkeypatch, state, expected):
    request = AsyncMock(return_value=lifecycle(state))
    monkeypatch.setattr("helpers.request_agent_json", request)
    result = await check_service_health("opencode", OPENCODE_CONFIG)
    assert result.status == expected
    request.assert_awaited_once_with("GET", "/v1/opencode/status", timeout=10)
    assert helpers.get_opencode_lifecycle()["state"] == state


@pytest.mark.asyncio
async def test_older_host_agent_falls_back_to_the_port_proof(monkeypatch):
    calls = []

    async def fake(method, path, **kwargs):
        calls.append(path)
        if path == "/v1/opencode/status":
            raise AgentHTTPError(404, "Not found")
        return {"reachable": True, "response_time_ms": 3.0}

    monkeypatch.setattr("helpers.request_agent_json", fake)
    result = await check_service_health("opencode", OPENCODE_CONFIG)
    assert calls == ["/v1/opencode/status", "/v1/host/port"]
    assert result.status == "healthy"
    assert helpers.get_opencode_lifecycle() is None


@pytest.mark.asyncio
async def test_unknown_state_fails_closed(monkeypatch):
    monkeypatch.setattr("helpers.request_agent_json", AsyncMock(return_value={"state": "exploded"}))
    result = await check_service_health("opencode", OPENCODE_CONFIG)
    assert result.status == "down"
    assert helpers.get_opencode_lifecycle() is None


@pytest.mark.asyncio
async def test_confirmed_stopped_opencode_stays_visible_in_the_cache(monkeypatch):
    monkeypatch.setattr(helpers, "SERVICES", {"opencode": OPENCODE_CONFIG})
    monkeypatch.setattr("helpers.request_agent_json", AsyncMock(return_value=lifecycle("stopped")))
    set_services_cache([await check_service_health("opencode", OPENCODE_CONFIG)])
    assert get_cached_services()[0].status == "down"


@pytest.mark.asyncio
async def test_unreachable_agent_still_hides_optional_opencode(monkeypatch):
    monkeypatch.setattr(helpers, "SERVICES", {"opencode": OPENCODE_CONFIG})
    monkeypatch.setattr("helpers.request_agent_json", AsyncMock(side_effect=AgentUnavailable("down")))
    set_services_cache([await check_service_health("opencode", OPENCODE_CONFIG)])
    assert get_cached_services()[0].status == "not_deployed"


@pytest.mark.asyncio
async def test_other_host_services_keep_the_port_proof(monkeypatch):
    request = AsyncMock(return_value={"reachable": True, "response_time_ms": 2.0})
    monkeypatch.setattr("helpers.request_agent_json", request)
    config = {**OPENCODE_CONFIG, "name": "Pixel", "port": 9595, "external_port": 9595}
    result = await check_service_health("pixel-agent", config)
    assert result.status == "healthy"
    request.assert_awaited_once_with(
        "GET", "/v1/host/port", params={"host": "127.0.0.1", "port": 9595}, timeout=5,
    )


@pytest.mark.asyncio
async def test_refresh_replaces_only_the_opencode_row(monkeypatch):
    monkeypatch.setattr(helpers, "SERVICES", {"opencode": OPENCODE_CONFIG})
    other = ServiceStatus(id="n8n", name="n8n", port=5678, external_port=5678, status="healthy")
    stale = ServiceStatus(id="opencode", name="OpenCode (IDE)", port=3003, external_port=3003, status="down")
    monkeypatch.setattr(helpers, "_services_cache", [other, stale])
    monkeypatch.setattr("helpers.request_agent_json", AsyncMock(return_value=lifecycle("running")))
    await helpers.refresh_cached_service_status("opencode")
    assert [(item.id, item.status) for item in get_cached_services()] == [("n8n", "healthy"), ("opencode", "healthy")]


# --- /api/apps/opencode ----------------------------------------------------------


@pytest.fixture()
def app_api(test_client, monkeypatch, tmp_path):
    import routers.opencode_app as router

    monkeypatch.setattr(router, "SERVICES", {"opencode": dict(OPENCODE_CONFIG)})
    monkeypatch.setattr(router, "DATA_DIR", str(tmp_path))
    refresh = AsyncMock()
    monkeypatch.setattr(router, "refresh_cached_service_status", refresh)
    monkeypatch.setattr(router, "get_cached_services", lambda: [])
    test_client.router = router
    test_client.refresh = refresh
    test_client.data_dir = tmp_path
    return test_client


def write_progress(data_dir, status, error=None):
    directory = data_dir / "extension-progress"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "opencode.json").write_text(json.dumps({
        "service_id": "opencode", "status": status, "phase_label": "OpenCode setup failed" if error else "Downloading",
        "error": error, "started_at": "2026-09-25T00:00:00Z", "updated_at": "2026-09-25T00:00:01Z",
    }))


def test_status_requires_auth(app_api):
    assert app_api.get("/api/apps/opencode").status_code == 401


def test_status_shapes_the_host_lifecycle(app_api, monkeypatch):
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value=lifecycle("running")))
    response = app_api.get("/api/apps/opencode", headers=app_api.auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "id": "opencode",
        "name": "OpenCode",
        "state": "running",
        "running": True,
        "installed": True,
        "version": "1.18.32",
        "platform": "linux",
        "port": 3003,
        "portInUse": False,
        "startSupported": True,
        "setupSupported": False,
        "setupIssue": None,
        "localUrl": "http://localhost:3003/",
        "publicUrl": None,
        "progress": None,
    }


def test_status_carries_an_operator_public_url(app_api, monkeypatch):
    app_api.router.SERVICES["opencode"]["public_url"] = "https://code.example.test"
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value=lifecycle("running")))
    response = app_api.get("/api/apps/opencode", headers=app_api.auth_headers)
    assert response.json()["publicUrl"] == "https://code.example.test"


def test_status_shows_the_last_setup_failure_until_opencode_is_set_up(app_api, monkeypatch):
    write_progress(app_api.data_dir, "error", "OpenCode download or verification failed: offline")
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value=lifecycle("not_installed")))
    failed = app_api.get("/api/apps/opencode", headers=app_api.auth_headers).json()
    assert failed["progress"]["status"] == "error"
    assert "offline" in failed["progress"]["error"]

    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value=lifecycle("running")))
    assert app_api.get("/api/apps/opencode", headers=app_api.auth_headers).json()["progress"] is None


def test_status_shows_setup_progress_while_installing(app_api, monkeypatch):
    write_progress(app_api.data_dir, "pulling")
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value=lifecycle("installing")))
    body = app_api.get("/api/apps/opencode", headers=app_api.auth_headers).json()
    assert body["progress"] == {"status": "pulling", "phaseLabel": "Downloading", "error": None, "updatedAt": "2026-09-25T00:00:01Z"}


def test_status_explains_an_older_host_agent(app_api, monkeypatch):
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(side_effect=AgentHTTPError(404, "Not found")))
    response = app_api.get("/api/apps/opencode", headers=app_api.auth_headers)
    assert response.status_code == 503
    assert "ods agent restart" in response.json()["detail"]


def test_status_reports_an_unreachable_host_agent(app_api, monkeypatch):
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(side_effect=AgentUnavailable("refused")))
    response = app_api.get("/api/apps/opencode", headers=app_api.auth_headers)
    assert response.status_code == 503
    assert "not reachable" in response.json()["detail"]


def test_status_rejects_an_unknown_state(app_api, monkeypatch):
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(return_value={"state": "?"}))
    assert app_api.get("/api/apps/opencode", headers=app_api.auth_headers).status_code == 502


def test_start_forwards_to_the_host_and_refreshes_the_cache(app_api, monkeypatch):
    request = AsyncMock(return_value={"started": True, "status": lifecycle("running")})
    monkeypatch.setattr(app_api.router, "request_agent_json", request)
    monkeypatch.setattr(app_api.router, "get_cached_services", lambda: [object()])
    response = app_api.post("/api/apps/opencode/start", headers=app_api.auth_headers)
    assert response.status_code == 200
    assert response.json()["opencode"]["state"] == "running"
    request.assert_awaited_once_with("POST", "/v1/opencode/start", timeout=150)
    app_api.refresh.assert_awaited_once_with("opencode")


def test_start_requires_auth(app_api, monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(app_api.router, "request_agent_json", request)
    assert app_api.post("/api/apps/opencode/start").status_code == 401
    request.assert_not_awaited()


def test_start_conflict_carries_the_host_reason(app_api, monkeypatch):
    body = {"error": "OpenCode is not set up on this ODS installation", "code": "opencode_not_installed",
            "status": lifecycle("not_installed")}
    error = AgentHTTPError(409, body["error"], json.dumps(body))
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(side_effect=error))
    response = app_api.post("/api/apps/opencode/start", headers=app_api.auth_headers)
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "opencode_not_installed"
    assert payload["detail"] == body["error"]
    assert payload["opencode"]["state"] == "not_installed"


def test_start_failure_is_a_bad_gateway(app_api, monkeypatch):
    body = {"error": "Managed OpenCode did not become healthy", "code": "opencode_start_failed", "status": lifecycle("stopped")}
    monkeypatch.setattr(app_api.router, "request_agent_json", AsyncMock(side_effect=AgentHTTPError(502, body["error"], json.dumps(body))))
    response = app_api.post("/api/apps/opencode/start", headers=app_api.auth_headers)
    assert response.status_code == 502
    assert response.json()["opencode"]["state"] == "stopped"


def test_setup_is_accepted_in_the_background(app_api, monkeypatch):
    request = AsyncMock(return_value={"accepted": True, "status": lifecycle("installing")})
    monkeypatch.setattr(app_api.router, "request_agent_json", request)
    response = app_api.post("/api/apps/opencode/setup", headers=app_api.auth_headers)
    assert response.status_code == 202
    assert response.json()["opencode"]["state"] == "installing"
    request.assert_awaited_once_with("POST", "/v1/opencode/setup", timeout=30)


# --- extension library -----------------------------------------------------------


def _opencode_catalog_entry():
    return {
        "id": "opencode", "name": "OpenCode (IDE)", "description": "AI-powered code editor in your browser",
        "category": "optional", "gpu_backends": ["all"], "compose_file": "", "depends_on": [],
        "port": 3003, "external_port_default": 3003, "health_endpoint": "/", "env_vars": [],
        "tags": [], "features": [], "catalog_source": "builtin",
    }


def _patch_library(monkeypatch, tmp_path):
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", [_opencode_catalog_entry()])
    monkeypatch.setattr("routers.extensions.SERVICES", {"opencode": OPENCODE_CONFIG})
    monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", tmp_path / "lib")
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", tmp_path / "builtin")
    monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))


def _opencode_row(status):
    return ServiceStatus(id="opencode", name="OpenCode (IDE)", port=3003, external_port=3003, status=status)


@pytest.mark.parametrize("service_status,expected", [
    ("healthy", "enabled"),
    ("degraded", "installing"),
    ("down", "stopped"),
    ("not_deployed", "not_installed"),
    ("unknown", "disabled"),
])
def test_library_status_follows_the_host_lifecycle(monkeypatch, tmp_path, service_status, expected):
    from routers.extensions import _compute_extension_status

    _patch_library(monkeypatch, tmp_path)
    status = _compute_extension_status(_opencode_catalog_entry(), {"opencode": _opencode_row(service_status)})
    assert status == expected


def test_library_shows_a_failed_setup_until_it_succeeds(monkeypatch, tmp_path):
    from routers.extensions import _compute_extension_status

    _patch_library(monkeypatch, tmp_path)
    write_progress(tmp_path, "error", "offline")
    entry = _opencode_catalog_entry()
    assert _compute_extension_status(entry, {"opencode": _opencode_row("not_deployed")}) == "error"
    assert _compute_extension_status(entry, {"opencode": _opencode_row("healthy")}) == "enabled"


def test_catalog_offers_linux_setup_and_the_app_page(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)
    monkeypatch.setattr(helpers, "_opencode_lifecycle", lifecycle("not_installed"))
    with patch("helpers.get_cached_services", return_value=[_opencode_row("not_deployed")]):
        response = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers)
    entry = response.json()["extensions"][0]
    assert entry["status"] == "not_installed"
    assert entry["installable"] is True
    assert entry["app_path"] == "/apps/opencode"
    assert entry["source"] == "core"


def test_catalog_does_not_offer_setup_where_it_is_unsupported(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)
    monkeypatch.setattr(helpers, "_opencode_lifecycle", lifecycle(
        "not_installed", setupSupported=False, setupIssue="needs systemd",
    ))
    with patch("helpers.get_cached_services", return_value=[_opencode_row("not_deployed")]):
        entry = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers).json()["extensions"][0]
    assert entry["installable"] is False
    assert entry["setup_issue"] == "needs systemd"


def test_detail_replaces_the_inapplicable_cli_instructions(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)
    monkeypatch.setattr(helpers, "_opencode_lifecycle", lifecycle("stopped"))
    with patch("helpers.get_cached_services", return_value=[_opencode_row("down")]):
        detail = test_client.get("/api/extensions/opencode", headers=test_client.auth_headers).json()
    assert detail["status"] == "stopped"
    assert detail["app_path"] == "/apps/opencode"
    assert "cli_enable" not in detail["setup_instructions"]
    assert all("ods enable" not in step for step in detail["setup_instructions"]["steps"])


def test_library_install_sets_opencode_up_on_the_host(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)
    calls = []

    def fake(method, path, **kwargs):
        calls.append((method, path))
        return {"accepted": True, "status": lifecycle("installing")}

    monkeypatch.setattr("routers.extensions.request_agent_json", fake)
    response = test_client.post("/api/extensions/opencode/install", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json() == {"id": "opencode", "action": "setup", "state": "installing"}
    assert calls == [("POST", "/v1/opencode/setup")]


def test_library_start_falls_back_to_setup_when_never_installed(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)
    calls = []

    def fake(method, path, **kwargs):
        calls.append(path)
        if path == "/v1/opencode/start":
            body = {"error": "OpenCode is not set up", "code": "opencode_not_installed"}
            raise AgentHTTPError(409, body["error"], json.dumps(body))
        return {"accepted": True, "status": lifecycle("installing")}

    monkeypatch.setattr("routers.extensions.request_agent_json", fake)
    response = test_client.post("/api/extensions/opencode/enable", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert calls == ["/v1/opencode/start", "/v1/opencode/setup"]


def test_library_start_reports_host_failures(test_client, monkeypatch, tmp_path):
    _patch_library(monkeypatch, tmp_path)

    def fake(method, path, **kwargs):
        raise AgentHTTPError(502, "Could not start OpenCode: unit failed", "{}")

    monkeypatch.setattr("routers.extensions.request_agent_json", fake)
    response = test_client.post("/api/extensions/opencode/enable", headers=test_client.auth_headers)
    assert response.status_code == 502
    assert "unit failed" in response.json()["detail"]
