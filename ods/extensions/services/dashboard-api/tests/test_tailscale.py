"""Tests for routers/tailscale.py, the proxy in front of the host-agent's
Tailscale status endpoint.

Mocked surfaces:
  * routers.tailscale.request_agent_json - stand-in for the shared transport.

The actual container/native `tailscale status --json` behavior lives at the
host-agent layer and is not reproducible here.
"""

import json
from unittest.mock import patch

from host_agent_client import AgentHTTPError, AgentTimeout, AgentUnavailable


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_tailscale_status_requires_auth(test_client):
    resp = test_client.get("/api/tailscale/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_agent_http_error(status, body):
    return AgentHTTPError(
        status,
        body.get("error", f"Host agent returned HTTP {status}"),
        json.dumps(body),
    )


# ---------------------------------------------------------------------------
# Three normal status shapes (all 200)
# ---------------------------------------------------------------------------


def test_status_when_extension_not_running(test_client):
    """Container not started → running=false. Not an error — the user just
    hasn't enabled the extension yet."""
    upstream = {"running": False}
    with patch("routers.tailscale.request_agent_json", return_value=upstream):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False


def test_status_when_running_but_not_authenticated(test_client):
    """Container up but TS_AUTHKEY is empty / rejected → user-facing reason."""
    upstream = {
        "running": True,
        "authenticated": False,
        "reason": "Tailscale is running but not yet authenticated. Set TS_AUTHKEY and restart.",
    }
    with patch("routers.tailscale.request_agent_json", return_value=upstream):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["authenticated"] is False
    assert "TS_AUTHKEY" in body["reason"]


def test_status_when_fully_joined(test_client):
    """Happy path — device is on the tailnet."""
    upstream = {
        "running": True,
        "authenticated": True,
        "backend_state": "Running",
        "self": {
            "hostname": "ods",
            "dns_name": "ods.tail-abcde.ts.net",
            "ips": ["100.64.0.42", "fd7a:115c:a1e0::42"],
            "online": True,
        },
        "magic_dns_suffix": "tail-abcde.ts.net",
        "tailnet_name": "example.com",
    }
    with patch("routers.tailscale.request_agent_json", return_value=upstream):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["self"]["dns_name"].endswith(".ts.net")
    assert "100.64.0.42" in body["self"]["ips"]
    assert body["magic_dns_suffix"] == "tail-abcde.ts.net"


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def test_status_returns_503_when_agent_unreachable(test_client):
    with patch(
        "routers.tailscale.request_agent_json",
        side_effect=AgentUnavailable("connection refused"),
    ):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 503


def test_status_returns_504_when_agent_request_times_out(test_client):
    with patch(
        "routers.tailscale.request_agent_json",
        side_effect=AgentTimeout("timed out"),
    ):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 504


def test_status_passes_through_504_timeout(test_client):
    err = _mock_agent_http_error(504, {"error": "docker exec timed out"})
    with patch("routers.tailscale.request_agent_json", side_effect=err):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


def test_status_passes_through_500_when_agent_errors(test_client):
    err = _mock_agent_http_error(500, {"error": "docker exec failed: ENOENT"})
    with patch("routers.tailscale.request_agent_json", side_effect=err):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 500
