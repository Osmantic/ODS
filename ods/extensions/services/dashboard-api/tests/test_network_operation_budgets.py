"""Exercise dashboard response budgets against real host HTTP round trips."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
import time

import httpx
import pytest

import host_agent_client
from routers import setup
from test_host_agent import _FakeHandler, _mod


@pytest.fixture
def host_http(test_client, monkeypatch):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/nmcli" if name == "nmcli" else None)
    completed = []
    disconnected = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def dispatch(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            host = _FakeHandler(body)
            method = {
                "/v1/network/status": _mod.AgentHandler._handle_network_status,
                "/v1/network/wifi-forget": _mod.AgentHandler._handle_network_wifi_forget,
            }[self.path]
            method(host)
            completed.append(host.parse_response())
            self.send_response(host.response_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(host.wfile.getvalue())
            except BrokenPipeError:
                # The baseline client times out before a successful host mutation.
                disconnected.append(self.path)

        do_GET = dispatch
        do_POST = dispatch

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
        monkeypatch.setattr(host_agent_client, "_get_sync_client", lambda: client)
        monkeypatch.setattr(setup, "request_agent_json", host_agent_client.request_json)
        try:
            yield completed
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


def test_forget_returns_success_when_both_host_steps_finish_within_their_limits(
    host_http, test_client, monkeypatch,
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        # Both commands finish within their production 10s/15s limits, but
        # their combined duration exceeds the original dashboard 15s deadline.
        time.sleep(8)
        output = "connection.type:802-11-wireless\n" if "show" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    response = test_client.post("/api/setup/wifi-forget", json={"connection": "Cafe"},
                                headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"success": True, "connection": "Cafe"}
    assert len(commands) == 2
    assert host_http == [{"success": True, "connection": "Cafe"}]


def _address_block(device, last_octet):
    return (f"GENERAL.DEVICE:{device}\nIP4.ADDRESS[1]:192.0.2.{last_octet}/24\n"
            "IP4.GATEWAY:192.0.2.1\n")


@pytest.mark.parametrize("devices_count", [3, 24])
def test_status_collects_all_addresses_with_a_fixed_number_of_host_operations(
    host_http, test_client, monkeypatch, devices_count,
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if devices_count == 3:
            # Before batching, four valid 3.5s operations exceed the API's 10s.
            time.sleep(3.5)
        if command[-1] == "status":
            output = "".join(f"wlan{i}:wifi:connected:Cafe\\:{i}\n" for i in range(devices_count))
            output += "eth0:ethernet:disconnected:--\n"
        elif command[-1] == "show":
            output = "\n".join(_address_block(f"wlan{i}", i + 2) for i in range(devices_count))
            output += _address_block("eth0", 250)
        else:
            output = _address_block(command[-1], int(command[-1][4:]) + 2)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    response = test_client.get("/api/setup/network-status", headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["wifi_connected"] is True
    assert [(d["device"], d["connection"], d["ip"], d["gateway"]) for d in value["devices"]] == [
        (f"wlan{i}", f"Cafe:{i}", f"192.0.2.{i + 2}", "192.0.2.1") for i in range(devices_count)
    ]
    assert len(commands) == 2


@pytest.mark.parametrize("failure", ["timeout", "os-error", "nonzero"])
def test_status_keeps_connection_information_when_address_collection_fails(
    host_http, test_client, monkeypatch, failure,
):
    def run(command, **kwargs):
        if command[-1] == "status":
            return subprocess.CompletedProcess(command, 0, stdout="wlan0:wifi:connected:Cafe\n", stderr="")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "os-error":
            raise OSError("NetworkManager unavailable")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="NetworkManager unavailable")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    response = test_client.get("/api/setup/network-status", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["devices"] == [{
        "device": "wlan0", "type": "wifi", "state": "connected", "connection": "Cafe", "ip": "", "gateway": "",
    }]
