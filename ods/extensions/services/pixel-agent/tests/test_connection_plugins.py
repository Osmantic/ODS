"""Reviewed OpenClaw plugins the owner turns on for Portal, through the gated gateway change.

The real owner transaction writes Portal's gateway configuration and onboarding
contract; systemd, the gateway and OpenClaw's plugin registry are simulated as
in test_connection_coordinator.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

import json
import os

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, planner, plugins, status

tx = harness.tx  # The owner transaction under test, imported from the agent's host directory.
world = harness.world  # The custodian's fixture: fake systemd, gateway and Google token endpoint.


@pytest.fixture
def owner(world, monkeypatch):
    """OpenClaw resolves every reviewed plugin it is asked about; each check is recorded."""
    world.checked = []
    monkeypatch.setattr(tx, "_catalog_ready", lambda openclaw, chosen: world.checked.append(list(chosen)) or True)
    return world


def turn(world, enabled):
    return c.control(world.bridge, "connection-plugin", {"plugin": "memory-wiki", "enabled": enabled})


def live(world):
    return json.loads(world.bridge.config_path.read_text())


def contract(world):
    return json.loads(world.bridge.answers_path.read_text())


def test_a_plugin_turns_on_with_its_reviewed_configuration_and_off_without_a_trace(owner):
    before = live(owner)
    assert turn(owner, True) == {"outcome": "applied"}
    config = live(owner)
    assert plugins.enabled_in(config) == ["memory-wiki"]
    assert config["plugins"]["entries"]["memory-wiki"] == {"enabled": True, "config": plugins.MEMORY_WIKI["config"]}
    assert set(plugins.MEMORY_WIKI["tools"]) <= set(config["tools"]["sandbox"]["tools"]["allow"])
    assert owner.checked[-1] == ["memory-wiki"], "OpenClaw resolved the plugin before Portal restarted"
    assert harness.record(owner)["plugins"] == {"memory-wiki": True}
    assert contract(owner)["portalPlugins"] == plugins.answers(["memory-wiki"]), "every later render keeps it"
    assert owner.bridge.log.count("restart") == 1
    # Asking again restarts nothing.
    assert turn(owner, True) == {"outcome": "unchanged"}
    assert owner.bridge.log.count("restart") == 1
    assert turn(owner, False) == {"outcome": "applied"}
    assert live(owner) == before
    assert "portalPlugins" not in contract(owner)
    assert harness.record(owner)["plugins"] == {"memory-wiki": False}


def test_a_busy_portal_keeps_the_previous_choice_and_nothing_waits_half_done(owner):
    owner.bridge.active = 1
    with pytest.raises(AccessError, match="runtime-busy"):
        turn(owner, True)
    assert not owner.record.exists(), "the owner had no record, so none is left"
    assert plugins.enabled_in(live(owner)) == [] and owner.bridge.log.count("restart") == 0
    owner.bridge.active = 0
    assert turn(owner, True) == {"outcome": "applied"}


def test_a_plugin_openclaw_cannot_load_is_rolled_back_and_stays_off(owner, monkeypatch):
    monkeypatch.setattr(tx, "_catalog_ready", lambda openclaw, chosen: not chosen)
    before = live(owner)
    with pytest.raises(AccessError, match="plugin-change-rolled-back"):
        turn(owner, True)
    assert live(owner) == before and not owner.record.exists()


def test_google_and_plugins_change_independently(owner):
    assert turn(owner, True) == {"outcome": "applied"}
    harness.connect(owner)
    assert harness.record(owner)["plugins"] == {"memory-wiki": True}
    assert c.control(owner.bridge, "connection-activate", {}) == {"outcome": "applied"}
    config = live(owner)
    assert planner.PLUGIN_ID in config["plugins"]["allow"] and plugins.enabled_in(config) == ["memory-wiki"]
    # Disconnecting Google leaves the plugin, and the record that keeps it.
    c.control(owner.bridge, "connection-disconnect", {"connector": "google"})
    assert harness.record(owner)["plugins"] == {"memory-wiki": True}
    assert c.control(owner.bridge, "connection-activate", {}) == {"outcome": "applied"}
    config = live(owner)
    assert planner.PLUGIN_ID not in config["plugins"]["allow"] and plugins.enabled_in(config) == ["memory-wiki"]


def test_only_a_reviewed_plugin_can_be_turned_on(owner):
    for request in ({"plugin": "browser", "enabled": True}, {"plugin": "memory-wiki"},
                    {"plugin": "memory-wiki", "enabled": "yes"}, {"plugin": "memory-wiki", "enabled": True, "x": 1}):
        with pytest.raises(AccessError, match="invalid-connection-request"):
            c.control(owner.bridge, "connection-plugin", request)
    assert not owner.record.exists() and owner.bridge.log.count("restart") == 0


def test_status_says_which_plugins_are_on_and_nothing_else(owner):
    def report():
        return status.validate_public(status.report(
            surface="linux-systemd", record_path=owner.record, token_path=owner.paths.token,
            status_path=owner.paths.projection / "status.json", lifecycle_path=owner.paths.state / "missing.json",
            owners=(0, os.getuid()), broker_state=owner.paths.state))
    assert report()["plugins"] == {"memory-wiki": False}
    turn(owner, True)
    assert report()["plugins"] == {"memory-wiki": True}
    assert "vaultMode" not in json.dumps(report())
