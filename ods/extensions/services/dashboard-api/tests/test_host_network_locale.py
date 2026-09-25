"""NetworkManager localizes output; host API parsing must use a fixed locale."""

import ctypes.util
import json
import os
import subprocess
import sys

import pytest

from test_host_agent import _FakeHandler, _mod


@pytest.fixture
def network_environment(monkeypatch):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod.shutil, "which", lambda name: "/usr/bin/nmcli" if name == "nmcli" else None)
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/run/test-network-bus")


def record_nmcli(monkeypatch, respond):
    calls = []

    def run(command, **kwargs):
        assert command[0] == "nmcli"
        environment = kwargs.get("env", os.environ)
        assert environment["DBUS_SYSTEM_BUS_ADDRESS"] == "unix:path=/run/test-network-bus"
        calls.append(command)
        english = environment.get("LC_ALL") == "C" or (
            environment.get("LC_ALL") == "C.UTF-8" and environment.get("LANGUAGE") == "C"
        )
        return respond(command, english)

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("locale", ["C", "fr_FR.UTF-8"])
def test_network_status_reports_connected_device_on_localized_host(
    network_environment, monkeypatch, locale,
):
    monkeypatch.setenv("LC_ALL", locale)

    def respond(command, english):
        if command[-1] == "status":
            state = "connected" if english else "connecté"
            output = f"wlan0:wifi:{state}:Café\\:réseau\n"
        else:
            assert command[-2:] == ["device", "show"]
            output = "GENERAL.DEVICE:wlan0\nIP4.ADDRESS[1]:192.0.2.5/24\nIP4.GATEWAY:192.0.2.1\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    calls = record_nmcli(monkeypatch, respond)
    handler = _FakeHandler(b"")
    _mod.AgentHandler._handle_network_status(handler)
    assert handler.response_code == 200
    assert handler.parse_response() == {
        "platform_supported": True, "wifi_connected": True,
        "devices": [{"device": "wlan0", "type": "wifi", "state": "connected",
                     "connection": "Café:réseau", "ip": "192.0.2.5", "gateway": "192.0.2.1"}],
    }
    assert len(calls) == 2
    assert os.environ["LC_ALL"] == locale
    assert os.environ["LANGUAGE"] == "fr"


def test_wrong_password_mapping_is_stable_on_localized_host(network_environment, monkeypatch):
    def respond(command, english):
        assert command[:4] == ["nmcli", "device", "wifi", "connect"]
        error = "Secrets were required, but not provided" if english else "Des secrets étaient nécessaires"
        return subprocess.CompletedProcess(command, 4, stdout="", stderr=error)

    record_nmcli(monkeypatch, respond)
    handler = _FakeHandler(json.dumps({"ssid": "Café", "password": "fixture-password"}).encode())
    _mod.AgentHandler._handle_network_wifi_connect(handler)
    assert handler.response_code == 400
    assert handler.parse_response() == {"error": "Wrong password", "code": 4}
    assert os.environ["LC_ALL"] == "fr_FR.UTF-8"


def test_missing_profile_stays_404_without_deletion_on_localized_host(network_environment, monkeypatch):
    def respond(command, english):
        assert command[-3:] == ["connection", "show", "Café"]
        error = "Error: unknown connection 'Café'" if english else "Erreur : connexion inconnue « Café »"
        return subprocess.CompletedProcess(command, 10, stdout="", stderr=error)

    calls = record_nmcli(monkeypatch, respond)
    handler = _FakeHandler(json.dumps({"connection": "Café"}).encode())
    _mod.AgentHandler._handle_network_wifi_forget(handler)
    assert handler.response_code == 404
    assert handler.parse_response()["error"] == "No such connection: Café"
    assert len(calls) == 1
    assert os.environ["LC_ALL"] == "fr_FR.UTF-8"


def test_network_names_survive_real_glib_output_encoding(network_environment, monkeypatch):
    library = ctypes.util.find_library("glib-2.0")
    if not library:
        pytest.skip("GLib is required for the NetworkManager output encoding probe")
    run = _mod.subprocess.run
    program = (
        "import ctypes, locale, sys\n"
        "locale.setlocale(locale.LC_ALL, '')\n"
        "glib = ctypes.CDLL(sys.argv[1])\n"
        "glib.g_print(b'%s\\n', 'wlan0:wifi:connected:Café'.encode('utf-8'))\n"
    )

    def child(command, **kwargs):
        if command[-1] == "status":
            return run([sys.executable, "-c", program, library], **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", child)
    handler = _FakeHandler(b"")
    _mod.AgentHandler._handle_network_status(handler)
    assert handler.response_code == 200
    assert handler.parse_response()["devices"][0]["connection"] == "Café"
