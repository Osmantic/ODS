"""Tests for the Wi-Fi / network proxy endpoints in routers/setup.py.

These cover the dashboard-api → host-agent forwarding path. The actual
nmcli interaction is tested at the host-agent layer (which lives outside
this test suite — Wi-Fi mutation isn't reproducible in CI).

Mocked surfaces:
  * urllib.request.urlopen — stand in for the host-agent HTTP call. The
    proxy functions are sync (run via asyncio.to_thread), so a regular
    MagicMock context-manager is the right shape.
"""

import json
from unittest.mock import patch, MagicMock

import urllib.error


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_wifi_scan_requires_auth(test_client):
    resp = test_client.get("/api/setup/wifi-scan")
    assert resp.status_code == 401


def test_wifi_connect_requires_auth(test_client):
    resp = test_client.post("/api/setup/wifi-connect", json={"ssid": "x", "password": ""})
    assert resp.status_code == 401


def test_network_status_requires_auth(test_client):
    resp = test_client.get("/api/setup/network-status")
    assert resp.status_code == 401


def test_wifi_forget_requires_auth(test_client):
    resp = test_client.post("/api/setup/wifi-forget", json={"connection": "Home"})
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
        url="http://agent/v1/...",
        code=status,
        msg="error",
        hdrs=None,
        fp=None,
    )
    err.read = lambda: json.dumps(body).encode("utf-8")
    return err


# ---------------------------------------------------------------------------
# wifi-scan
# ---------------------------------------------------------------------------


def test_wifi_scan_happy_path(test_client):
    upstream = {
        "networks": [
            {"ssid": "Home WiFi", "signal": 88, "security": "WPA2", "in_use": True},
            {"ssid": "Guest",     "signal": 50, "security": "WPA2", "in_use": False},
        ]
    }
    with patch("routers.setup.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
        resp = test_client.get("/api/setup/wifi-scan", headers=test_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["networks"]) == 2
    assert body["networks"][0]["ssid"] == "Home WiFi"


def test_wifi_scan_translates_501_when_platform_unsupported(test_client):
    err = _mock_agent_http_error(501, {"error": "Wi-Fi management only supported on Linux (this is Windows)"})
    with patch("routers.setup.urllib.request.urlopen", side_effect=err):
        resp = test_client.get("/api/setup/wifi-scan", headers=test_client.auth_headers)
    assert resp.status_code == 501
    assert "Linux" in resp.json()["detail"]


def test_wifi_scan_returns_503_when_agent_unreachable(test_client):
    with patch(
        "routers.setup.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        resp = test_client.get("/api/setup/wifi-scan", headers=test_client.auth_headers)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# wifi-connect
# ---------------------------------------------------------------------------


def test_wifi_connect_happy_path(test_client):
    with patch(
        "routers.setup.urllib.request.urlopen",
        return_value=_mock_agent_response({"success": True, "ssid": "Home WiFi"}),
    ):
        resp = test_client.post(
            "/api/setup/wifi-connect",
            json={"ssid": "Home WiFi", "password": "supersecret"},
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_wifi_connect_rejects_oversized_ssid(test_client):
    resp = test_client.post(
        "/api/setup/wifi-connect",
        json={"ssid": "x" * 33, "password": ""},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 422


def test_wifi_connect_rejects_oversized_password(test_client):
    resp = test_client.post(
        "/api/setup/wifi-connect",
        json={"ssid": "ok", "password": "x" * 64},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 422


def test_wifi_connect_rejects_control_chars_in_ssid(test_client):
    resp = test_client.post(
        "/api/setup/wifi-connect",
        json={"ssid": "bad\nssid", "password": ""},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 422


def test_wifi_connect_translates_wrong_password(test_client):
    err = _mock_agent_http_error(400, {"error": "Wrong password", "code": 7})
    with patch("routers.setup.urllib.request.urlopen", side_effect=err):
        resp = test_client.post(
            "/api/setup/wifi-connect",
            json={"ssid": "Home WiFi", "password": "wrong"},
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 400
    assert "Wrong password" in resp.json()["detail"]


def test_wifi_connect_translates_504_timeout(test_client):
    err = _mock_agent_http_error(504, {"error": "Connection attempt timed out"})
    with patch("routers.setup.urllib.request.urlopen", side_effect=err):
        resp = test_client.post(
            "/api/setup/wifi-connect",
            json={"ssid": "Home WiFi", "password": "p"},
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# network-status
# ---------------------------------------------------------------------------


def test_network_status_happy_path(test_client):
    upstream = {
        "platform_supported": True,
        "devices": [
            {"device": "wlan0", "type": "wifi", "state": "connected",
             "connection": "Home WiFi", "ip": "192.168.1.42", "gateway": "192.168.1.1"},
        ],
        "wifi_connected": True,
    }
    with patch("routers.setup.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
        resp = test_client.get("/api/setup/network-status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_supported"] is True
    assert body["wifi_connected"] is True
    assert body["devices"][0]["ip"] == "192.168.1.42"


def test_network_status_unsupported_platform(test_client):
    # The host-agent returns 200 with platform_supported=false rather than 501
    # here, so the wizard can render a fallback without error-handling.
    upstream = {"platform_supported": False, "platform": "Windows", "reason": "..."}
    with patch("routers.setup.urllib.request.urlopen", return_value=_mock_agent_response(upstream)):
        resp = test_client.get("/api/setup/network-status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["platform_supported"] is False


# ---------------------------------------------------------------------------
# wifi-forget
# ---------------------------------------------------------------------------


def test_wifi_forget_happy_path(test_client):
    with patch(
        "routers.setup.urllib.request.urlopen",
        return_value=_mock_agent_response({"success": True, "connection": "OldNetwork"}),
    ):
        resp = test_client.post(
            "/api/setup/wifi-forget",
            json={"connection": "OldNetwork"},
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_wifi_forget_rejects_control_chars(test_client):
    resp = test_client.post(
        "/api/setup/wifi-forget",
        json={"connection": "bad\nname"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 422
