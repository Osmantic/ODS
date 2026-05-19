"""Tests for routers/tailscale.py — the proxy in front of the host-agent's
Tailscale status endpoint.

Mocked surfaces:
  * urllib.request.urlopen — stand-in for the host-agent HTTP call.

The actual container/native `tailscale status --json` behavior lives at the
host-agent layer and is not reproducible here.
"""

import json
from unittest.mock import patch, MagicMock

import urllib.error


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_tailscale_status_requires_auth(test_client):
    resp = test_client.get("/api/tailscale/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_agent_response(body, status=200):
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _mock_agent_http_error(status, body):
    err = urllib.error.HTTPError(
        url="http://agent/v1/tailscale/status",
        code=status,
        msg="error",
        hdrs=None,
        fp=None,
    )
    err.read = lambda: json.dumps(body).encode("utf-8")
    return err


# ---------------------------------------------------------------------------
# Three normal status shapes (all 200)
# ---------------------------------------------------------------------------


def test_status_when_extension_not_running(test_client):
    """Container not started → running=false. Not an error — the user just
    hasn't enabled the extension yet."""
    upstream = {"running": False}
    with patch("routers.tailscale.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
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
    with patch("routers.tailscale.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
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
            "hostname": "dream",
            "dns_name": "dream.tail-abcde.ts.net",
            "ips": ["100.64.0.42", "fd7a:115c:a1e0::42"],
            "online": True,
        },
        "magic_dns_suffix": "tail-abcde.ts.net",
        "tailnet_name": "example.com",
    }
    with patch("routers.tailscale.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
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
        "routers.tailscale.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 503


def test_status_returns_504_when_agent_request_times_out(test_client):
    with patch(
        "routers.tailscale.urllib.request.urlopen",
        side_effect=TimeoutError("timed out"),
    ):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 504


def test_status_passes_through_504_timeout(test_client):
    err = _mock_agent_http_error(504, {"error": "docker exec timed out"})
    with patch("routers.tailscale.urllib.request.urlopen", side_effect=err):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


def test_status_passes_through_500_when_agent_errors(test_client):
    err = _mock_agent_http_error(500, {"error": "docker exec failed: ENOENT"})
    with patch("routers.tailscale.urllib.request.urlopen", side_effect=err):
        resp = test_client.get("/api/tailscale/status", headers=test_client.auth_headers)
    assert resp.status_code == 500
