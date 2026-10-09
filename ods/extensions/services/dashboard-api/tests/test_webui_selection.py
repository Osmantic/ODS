"""Dedicated WebUI add-back route stays authenticated and host-owned."""

from unittest.mock import AsyncMock, patch

import pytest

import helpers
from models import ServiceStatus

from host_agent_client import AgentHTTPError


def test_selection_requires_dashboard_auth(test_client):
    with patch("routers.extensions.request_agent_json") as agent:
        response = test_client.get("/api/webui/selection")
        assert response.status_code in {401, 403}
        agent.assert_not_called()


def test_selection_reports_only_public_booleans(test_client):
    with patch("routers.extensions.request_agent_json", return_value={
        "enabled": False, "supported": True, "private": "never expose",
    }) as agent:
        response = test_client.get("/api/webui/selection", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "supported": True}
    assert response.headers["cache-control"] == "no-store"
    agent.assert_called_once_with("GET", "/v1/webui/selection", timeout=5)


def test_add_back_accepts_only_explicit_enable_and_proxies_to_host(test_client):
    with patch("routers.extensions.request_agent_json", return_value={
        "enabled": True, "action": "enabled", "private": "never expose",
    }) as agent:
        invalid = test_client.post("/api/webui/selection", json={"enabled": False}, headers=test_client.auth_headers)
        assert invalid.status_code == 400
        agent.assert_not_called()
        response = test_client.post("/api/webui/selection", json={"enabled": True}, headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "action": "enabled"}
    assert response.headers["cache-control"] == "no-store"
    agent.assert_called_once_with("POST", "/v1/webui/selection", payload={"enabled": True}, timeout=900)


@pytest.mark.parametrize(
    "enabled,action,stale_status,fresh_status",
    [
        (True, "enabled", "not_deployed", "healthy"),
        (True, "already_selected", "down", "healthy"),
        (True, "enabled", "not_deployed", "unhealthy"),
    ],
)
def test_selection_action_refreshes_only_webui_cached_health(
    test_client, monkeypatch, enabled, action, stale_status, fresh_status,
):
    """The immediate Library catalog/detail fetch must see the owner action."""
    webui = ServiceStatus(id="open-webui", name="Open WebUI", port=8080,
                          external_port=3000, status=stale_status)
    other = ServiceStatus(id="dashboard-api", name="Dashboard API", port=3002,
                          external_port=3002, status="healthy")
    monkeypatch.setattr(helpers, "_services_cache", [other, webui])
    monkeypatch.setattr(helpers, "SERVICES", {"open-webui": {
        "name": "Open WebUI", "host": "open-webui", "port": 8080,
        "external_port": 3000, "health": "/health", "type": "docker",
    }})
    probe = AsyncMock(return_value=ServiceStatus(
        id="open-webui", name="Open WebUI", port=8080,
        external_port=3000, status=fresh_status,
    ))
    monkeypatch.setattr(helpers, "check_service_health", probe)
    with patch("routers.extensions.request_agent_json", return_value={
        "enabled": enabled, "action": action,
    }):
        response = test_client.post("/api/webui/selection", json={"enabled": enabled},
                                    headers=test_client.auth_headers)
    assert response.status_code == 200
    assert [(row.id, row.status) for row in helpers.get_cached_services()] == [
        ("dashboard-api", "healthy"), ("open-webui", fresh_status),
    ]
    probe.assert_awaited_once()


def test_add_rejects_an_unverified_agent_result(test_client):
    with patch("routers.extensions.request_agent_json", return_value={"enabled": False, "action": "enabled"}), \
         patch("helpers.refresh_cached_service_status", new_callable=AsyncMock) as refresh:
        response = test_client.post("/api/webui/selection", json={"enabled": True}, headers=test_client.auth_headers)
    assert response.status_code == 502
    refresh.assert_not_awaited()


def test_refresh_failure_does_not_reissue_a_successful_owner_action(test_client):
    with patch("routers.extensions.request_agent_json", return_value={
        "enabled": True, "action": "enabled",
    }) as agent, patch("helpers.refresh_cached_service_status", new_callable=AsyncMock,
                       side_effect=RuntimeError("private health detail")):
        response = test_client.post("/api/webui/selection", json={"enabled": True},
                                    headers=test_client.auth_headers)
    assert response.status_code == 200
    assert "private health detail" not in response.text
    agent.assert_called_once()


def test_add_back_reconciliation_failure_is_not_reported_as_success(test_client):
    with patch("routers.extensions.request_agent_json", side_effect=AgentHTTPError(503, "private host detail")):
        response = test_client.post("/api/webui/selection", json={"enabled": True}, headers=test_client.auth_headers)
    assert response.status_code == 503
    assert "inspection" in response.json()["detail"].lower()
    assert "private host detail" not in response.text


def test_unsupported_platform_message_is_not_linux_specific(test_client):
    with patch("routers.extensions.request_agent_json", side_effect=AgentHTTPError(501, "private host detail")):
        response = test_client.post("/api/webui/selection", json={"enabled": True}, headers=test_client.auth_headers)
    assert response.status_code == 501
    assert "unavailable on this platform" in response.json()["detail"]
    assert "private host detail" not in response.text
