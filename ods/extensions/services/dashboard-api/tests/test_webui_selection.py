"""Dedicated WebUI add-back route stays authenticated and host-owned."""

from unittest.mock import patch

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
