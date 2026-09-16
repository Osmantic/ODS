"""Keep the documented scan connection state when collapsing multiple BSSIDs."""

import subprocess

import pytest

from routers import setup
from test_host_agent import _FakeHandler, _mod


@pytest.mark.parametrize("connected_signal", [20, 80])
@pytest.mark.parametrize("connected_first", [True, False])
def test_scan_retains_connected_ssid_from_any_bssid(
    test_client, monkeypatch, connected_signal, connected_first,
):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/nmcli" if name == "nmcli" else None)
    rows = [f"Cafe\\:Mesh:{connected_signal}:WPA2:*", "Cafe\\:Mesh:80:WPA2:"]
    if not connected_first:
        rows.reverse()
    rows.extend(["Guest:40::", "Guest:25::", "Office:90:WPA3:"])
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["nmcli", "device", "wifi", "rescan"]:
            output = ""
        else:
            assert command == [
                "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
                "device", "wifi", "list",
            ]
            output = "\n".join(rows)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def host_transport(method, path, *, payload, timeout):
        assert (method, path, payload, timeout) == ("GET", "/v1/network/wifi-scan", None, 25)
        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_network_wifi_scan(handler)
        assert handler.response_code == 200
        return handler.parse_response()

    monkeypatch.setattr(_mod.subprocess, "run", run)
    monkeypatch.setattr(setup, "request_agent_json", host_transport)
    assert test_client.get("/api/setup/wifi-scan").status_code == 401
    assert commands == []

    response = test_client.get("/api/setup/wifi-scan", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["networks"] == [
        {"ssid": "Office", "signal": 90, "security": "WPA3", "in_use": False},
        {"ssid": "Cafe:Mesh", "signal": 80, "security": "WPA2", "in_use": True},
        {"ssid": "Guest", "signal": 40, "security": "open", "in_use": False},
    ]
    assert len(commands) == 2
