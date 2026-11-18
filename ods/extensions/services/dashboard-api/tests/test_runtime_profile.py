"""The runtime profile is parsed once and refuses unrunnable Desktop setups."""

import pytest

from runtime_profile import COMPOSE, WINDOWS_DESKTOP, RuntimeProfileError, is_loopback_host, parse

DESKTOP = {
    "ODS_RUNTIME_PROFILE": WINDOWS_DESKTOP,
    "ODS_DATA_DIR": r"C:\Users\José [QA]\AppData\Local\ODS\desktop\data",
    "DASHBOARD_API_KEY": "desktop-key",
}


def test_unset_profile_is_compose_with_every_capability():
    profile = parse({})
    assert profile.name == COMPOSE
    assert profile.host_agent and profile.child_processes and not profile.is_desktop


def test_desktop_profile_has_no_host_agent_or_child_processes():
    profile = parse(DESKTOP)
    assert profile.is_desktop
    assert profile.data_dir == DESKTOP["ODS_DATA_DIR"]
    assert not profile.host_agent
    assert not profile.child_processes


def test_desktop_listens_on_loopback_and_compose_on_every_interface():
    assert parse(DESKTOP).listen_host == "127.0.0.1"
    assert parse({}).listen_host == "0.0.0.0"


def test_unknown_profile_is_refused():
    with pytest.raises(RuntimeProfileError, match="not one of"):
        parse({"ODS_RUNTIME_PROFILE": "windows"})


@pytest.mark.parametrize("data_dir", ["", "   ", "data", r"ODS\data", "./data"])
def test_desktop_refuses_a_missing_or_relative_data_dir(data_dir):
    # A relative or missing directory would resolve against the launcher's
    # working directory or fall back to /data, the root of the current drive.
    with pytest.raises(RuntimeProfileError, match="ODS_DATA_DIR"):
        parse({**DESKTOP, "ODS_DATA_DIR": data_dir})


def test_desktop_refuses_a_missing_api_key():
    # Without the launcher's key the API would generate one nobody can read.
    with pytest.raises(RuntimeProfileError, match="DASHBOARD_API_KEY"):
        parse({**DESKTOP, "DASHBOARD_API_KEY": " "})


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]"])
def test_loopback_hosts(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["llama-server", "ods-webui", "host.docker.internal", "192.168.1.5", "0.0.0.0", ""])
def test_compose_and_network_hosts_are_not_loopback(host):
    assert not is_loopback_host(host)


def test_compose_keeps_its_existing_defaults():
    assert parse({"ODS_RUNTIME_PROFILE": "compose"}).data_dir is None
    assert parse({"ODS_DATA_DIR": "/data"}).data_dir == "/data"
