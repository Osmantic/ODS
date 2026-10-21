"""Creative Studio must add its optional WebUI through the host-owned path."""
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml
from fastapi import FastAPI

import helpers
import security
from host_agent_client import AgentHTTPError, AgentUnavailable
from routers import extensions, templates


@pytest.fixture
def refresh_webui(monkeypatch):
    refresh = AsyncMock()
    monkeypatch.setattr(helpers, "refresh_cached_service_status", refresh)
    return refresh


@pytest.fixture
def creative(monkeypatch, tmp_path, refresh_webui):
    source = Path(__file__).resolve().parents[4] / "templates/creative-studio.yaml"
    template = yaml.safe_load(source.read_text(encoding="utf-8"))["template"]
    monkeypatch.setattr(templates, "TEMPLATES", [template])
    monkeypatch.setattr(templates, "EXTENSION_CATALOG", [])
    monkeypatch.setattr(templates, "USER_EXTENSIONS_DIR", tmp_path / "extensions")
    monkeypatch.setattr(extensions, "_extension_operation_lock", lambda sid: nullcontext())
    monkeypatch.setattr(extensions, "_extensions_lock", nullcontext)
    monkeypatch.setattr(extensions, "_read_direct_deps", lambda sid: [])
    monkeypatch.setattr(templates, "_healthy_template_definition", lambda sid: True)
    statuses = {sid: "healthy" for sid in template["services"]}
    statuses["open-webui"] = "not_deployed"
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [
        SimpleNamespace(id=sid, status=status) for sid, status in statuses.items()
    ])
    agent = Mock(return_value={"enabled": False, "supported": True})
    monkeypatch.setattr(extensions, "request_agent_json", agent)
    generic = Mock(side_effect=AssertionError("WebUI must not use generic extension mutation"))
    for name in ("_activate_service", "_select_extensions_on_host", "_call_agent", "_call_agent_hook"):
        monkeypatch.setattr(extensions, name, generic)
    app = FastAPI()
    app.include_router(templates.router)
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "fixture-owner")
    return app, agent, statuses, generic


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture",
                             headers={"Authorization": "Bearer fixture-owner"})


@pytest.mark.asyncio
async def test_template_selection_requires_owner_auth(creative):
    app, agent, _, generic = creative
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as http:
        response = await http.post("/api/templates/creative-studio/apply")
    assert response.status_code in {401, 403}
    agent.assert_not_called()
    generic.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", ["not_deployed", "healthy"])
async def test_creative_preview_includes_unselected_webui_even_with_stale_health(creative, cached, refresh_webui):
    app, agent, statuses, generic = creative
    statuses["open-webui"] = cached
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/preview")
    assert response.status_code == 200
    assert response.json()["changes"]["to_enable"] == ["open-webui"]
    assert "open-webui" not in response.json()["changes"]["already_enabled"]
    agent.assert_called_once_with("GET", "/v1/webui/selection", timeout=5)
    generic.assert_not_called()
    refresh_webui.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,count", [("enabled", 1), ("already_selected", 0)])
async def test_creative_apply_uses_verified_webui_selection_once(creative, action, count, refresh_webui):
    app, agent, statuses, generic = creative
    # A stale healthy row must not bypass the installed choice on apply either.
    statuses["open-webui"] = "healthy"
    agent.return_value = {"enabled": True, "action": action}
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/apply")
    assert response.status_code == 200
    body = response.json()
    assert body["results"]["open-webui"] == ("enabled" if count else "already_enabled")
    assert body["enabled_count"] == body["started_count"] == count
    assert body["failed_services"] == body["skipped_services"] == []
    assert body["results"]["tts"] == "already_enabled"
    agent.assert_called_once_with("POST", "/v1/webui/selection", payload={"enabled": True}, timeout=900)
    generic.assert_not_called()
    refresh_webui.assert_awaited_once_with("open-webui")


@pytest.mark.asyncio
@pytest.mark.parametrize("health,refresh_fails", [("unhealthy", False), ("down", False), ("healthy", True)])
async def test_selected_webui_still_needs_health_before_template_is_available(
    creative, refresh_webui, health, refresh_fails,
):
    app, agent, statuses, generic = creative
    statuses["open-webui"] = health
    agent.return_value = {"enabled": True, "action": "already_selected"}
    if refresh_fails:
        refresh_webui.side_effect = RuntimeError("private probe detail")
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/apply")
    body = response.json()
    assert body["skipped_services"] == ["open-webui"]
    assert "health could not be confirmed" in body["results"]["open-webui"]
    assert body["enabled_count"] == body["started_count"] == 0
    assert "private" not in response.text
    agent.assert_called_once_with("POST", "/v1/webui/selection", payload={"enabled": True}, timeout=900)
    generic.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    AgentUnavailable("private transport detail"),
    AgentHTTPError(503, "private reconciliation detail"),
    AgentHTTPError(501, "private platform detail"),
    {"enabled": False, "action": "enabled"},
])
async def test_creative_failed_webui_apply_is_partial_without_generic_start(creative, failure, refresh_webui):
    app, agent, _, generic = creative
    if isinstance(failure, Exception):
        agent.side_effect = failure
    else:
        agent.return_value = failure
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/apply")
    assert response.status_code == 200
    body = response.json()
    assert body["skipped_services"] == ["open-webui"]
    assert body["results"]["open-webui"].startswith("skipped:")
    assert body["enabled_count"] == body["started_count"] == 0
    assert body["results"]["whisper"] == "already_enabled"
    assert "private" not in response.text
    assert agent.call_count == 1
    generic.assert_not_called()
    refresh_webui.assert_not_awaited()


@pytest.mark.asyncio
async def test_creative_preview_verifies_selection_without_claiming_unavailable_host(creative):
    app, agent, _, generic = creative
    agent.side_effect = AgentUnavailable("private transport detail")
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/preview")
    assert response.status_code == 503
    assert "private" not in response.text
    generic.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,supported,health,bucket", [
    (True, True, "healthy", "already_enabled"),
    (True, True, "down", "has_errors"),
    (False, False, "not_deployed", "incompatible"),
])
async def test_creative_preview_preserves_webui_readiness(creative, selected, supported, health, bucket):
    app, agent, statuses, _ = creative
    agent.return_value = {"enabled": selected, "supported": supported}
    statuses["open-webui"] = health
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/preview")
    assert response.status_code == 200
    assert "open-webui" in response.json()["changes"][bucket]
    assert "open-webui" not in response.json()["changes"]["to_enable"]


@pytest.mark.asyncio
async def test_webui_refusal_does_not_prevent_remaining_template_services(creative, monkeypatch):
    app, agent, statuses, _ = creative
    statuses["tts"] = "down"
    agent.side_effect = AgentHTTPError(503, "private reconciliation detail")
    monkeypatch.setattr(extensions, "_is_installable", lambda sid: False)
    monkeypatch.setattr(extensions, "_get_missing_deps_transitive", lambda sid: [])
    activate = Mock(return_value={"action": "enabled", "sha256": "a" * 64})
    select = Mock(return_value={"action": "enabled", "service_ids": ["tts"]})
    start = Mock(return_value=True)
    hook = Mock(return_value=True)
    monkeypatch.setattr(extensions, "_activate_service", activate)
    monkeypatch.setattr(extensions, "_select_extensions_on_host", select)
    monkeypatch.setattr(extensions, "_call_agent", start)
    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock())
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/apply")
    body = response.json()
    assert body["skipped_services"] == ["open-webui"]
    assert body["results"]["tts"] == "enabled"
    assert body["enabled_count"] == body["started_count"] == 1
    activate.assert_called_once_with("tts")
    start.assert_called_once_with("start", "tts")
    select.assert_called_once_with("enable", ["tts"], expected_sha256={"tts": "a" * 64})


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at,stop_succeeds", [(None, True), ("config", True), ("up", True), ("up", False)])
async def test_template_reuses_host_config_preservation_and_rollback(
    creative, monkeypatch, tmp_path, fail_at, stop_succeeds,
):
    """Run the real host action behind the API; only Docker is simulated."""
    app, _, _, generic = creative
    source = Path(__file__).resolve().parents[4] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("template_webui_fixture_host", source)
    host = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, host)
    spec.loader.exec_module(host)
    install = tmp_path / "install"
    install.mkdir()
    original = b"ENABLE_OPEN_WEBUI=false\nBIND_ADDRESS=127.0.0.1\nWEBUI_AUTH=false\nOWNER_SETTING=keep\n"
    environment = install / ".env"
    environment.write_bytes(original)
    inode = environment.stat().st_ino
    retained = install / "data/open-webui/retained-chat.db"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"retained chat")
    foreign = tmp_path / "foreign.env"
    foreign.write_bytes(b"foreign settings")
    monkeypatch.setattr(host, "INSTALL_DIR", install)
    monkeypatch.setattr(host, "EXTENSIONS_DIR", install / "extensions/services")
    monkeypatch.setattr(host, "USER_EXTENSIONS_DIR", install / "data/user-extensions")
    monkeypatch.setattr(host.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host, "resolve_compose_flags", lambda: ["-f", "base.yml"])
    monkeypatch.setattr(host, "_wait_for_container_health", lambda *a, **k: None)
    state = {"running": False}
    calls = []

    def docker(command, **kwargs):
        calls.append(command)
        if command[-2:] == ["config", "--services"]:
            return subprocess.CompletedProcess(command, int(fail_at == "config"), stdout="open-webui\n")
        if "up" in command:
            state["running"] = True
            return subprocess.CompletedProcess(command, int(fail_at == "up"))
        assert "stop" in command
        state["running"] = not stop_succeeds
        return subprocess.CompletedProcess(command, int(not stop_succeeds))

    monkeypatch.setattr(host.subprocess, "run", docker)
    monkeypatch.setattr(host, "_capture_container_state", lambda name: state.copy())

    def agent(method, path, *, payload, timeout):
        assert (method, path, payload, timeout) == ("POST", "/v1/webui/selection", {"enabled": True}, 900)
        status, result = host._enable_webui_selection()
        if status != 200:
            raise AgentHTTPError(status, result["error"])
        return result

    monkeypatch.setattr(extensions, "request_agent_json", agent)
    async with client(app) as http:
        response = await http.post("/api/templates/creative-studio/apply")
    body = response.json()
    assert response.status_code == 200
    assert body["skipped_services"] == ([] if fail_at is None else ["open-webui"])
    assert body["started_count"] == (1 if fail_at is None else 0)
    if fail_at is not None and stop_succeeds:
        assert environment.read_bytes() == original
        assert state["running"] is False
    else:
        assert host.load_env(environment)["ENABLE_OPEN_WEBUI"] == "true"
    assert host.load_env(environment)["OWNER_SETTING"] == "keep"
    assert environment.stat().st_ino == inode
    assert retained.read_bytes() == b"retained chat"
    assert foreign.read_bytes() == b"foreign settings"
    assert all("llama-server" not in command for command in calls)
    generic.assert_not_called()
