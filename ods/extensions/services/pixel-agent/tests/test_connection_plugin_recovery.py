"""Crash recovery around the real coordinator/owner transaction in isolated fixtures.

The acquiring cases kill an actual forked coordinator process. Later boundaries
use an uncaught BaseException so the in-process fake gateway retains its state.
Systemd and OpenClaw are simulated; this is not installed-gateway acceptance.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

import json
import os
from pathlib import Path
import signal
import time

import pytest

import test_connection_coordinator as harness
import test_connection_plugins as plugin_harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, lifecycle, plugins, state, status

owner, world = plugin_harness.owner, plugin_harness.world
turn, live = plugin_harness.turn, plugin_harness.live


def journal_path(owner):
    return Path(owner.bridge.state) / "transition.json"


def pending(owner):
    return lifecycle.read_operation(journal_path(owner), owners=c.OWNERS)


def die(*_args, **_kwargs):
    raise harness.Crash("injected process interruption")


def crash_before_settlement(owner, monkeypatch, enabled):
    with monkeypatch.context() as fault:
        fault.setattr(c, "_settle_plugin_record", die)
        with pytest.raises(harness.Crash):
            turn(owner, enabled)


def fork_at_first_journal(owner, enabled):
    """Only the child runs the coordinator; no exception unwinds its handlers."""
    child = os.fork()
    if child == 0:
        original = c._write

        def write_then_die(bridge, journal):
            original(bridge, journal)
            if journal["phase"] == "acquiring":
                os._exit(73)

        c._write = write_then_die
        try:
            turn(owner, enabled)
        except BaseException:
            os._exit(74)
        os._exit(75)
    reaped = False
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            done, result = os.waitpid(child, os.WNOHANG)
            if done:
                reaped = True
                assert os.WIFEXITED(result) and os.WEXITSTATUS(result) == 73
                return
            time.sleep(0.01)
        pytest.fail("private coordinator child did not exit within 15 seconds")
    finally:
        if not reaped:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)


@pytest.mark.parametrize("enabled", [True, False])
def test_process_death_after_journal_before_owner_write_is_visible_and_recoverable(owner, enabled):
    if not enabled:
        turn(owner, True)
    old_record, old_config = harness.record(owner), live(owner)
    fork_at_first_journal(owner, enabled)
    assert harness.record(owner) == old_record
    assert live(owner) == old_config
    assert pending(owner)["phase"] == "acquiring"
    public = harness.public_status(owner)
    assert public["pluginChange"] == {"plugin": "memory-wiki", "enabled": enabled, "error": None}
    assert public["plugins"] == {"memory-wiki": not enabled}
    # The existing recovery protocol safely rolls back a transaction whose owner
    # never wrote. A fresh request may then apply the requested choice.
    with pytest.raises(AccessError, match="plugin-change-rolled-back"):
        turn(owner, enabled)
    assert harness.record(owner) == old_record and live(owner) == old_config
    assert pending(owner) is None
    assert "pluginChange" not in harness.public_status(owner)
    assert turn(owner, enabled) == {"outcome": "applied"}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("recovery", ["matching-retry", "activate"])
def test_verified_gateway_before_record_write_recovers_admitted_choice(owner, monkeypatch, enabled, recovery):
    if not enabled:
        turn(owner, True)
    old_record = harness.record(owner)
    crash_before_settlement(owner, monkeypatch, enabled)
    assert harness.record(owner) == old_record
    assert plugins.enabled_in(live(owner)) == (["memory-wiki"] if enabled else [])
    assert pending(owner)["phase"] == "releasing"
    assert harness.public_status(owner)["pluginChange"] == {
        "plugin": "memory-wiki", "enabled": enabled, "error": None}
    restarts = owner.bridge.log.count("restart")
    result = turn(owner, enabled) if recovery == "matching-retry" else c.control(owner.bridge, "connection-activate", {})
    assert result == {"outcome": "applied"}
    assert harness.record(owner)["plugins"] == {"memory-wiki": enabled}
    assert pending(owner) is None and "pluginChange" not in harness.public_status(owner)
    assert owner.bridge.log.count("restart") == restarts
    assert turn(owner, enabled) == {"outcome": "unchanged"}
    assert owner.bridge.log.count("restart") == restarts


@pytest.mark.parametrize("enabled", [True, False])
def test_record_settled_before_release_is_not_written_or_restarted_twice(owner, monkeypatch, enabled):
    if not enabled:
        turn(owner, True)
    with monkeypatch.context() as fault:
        fault.setattr(c, "_release", die)
        with pytest.raises(harness.Crash):
            turn(owner, enabled)
    assert harness.record(owner)["plugins"] == {"memory-wiki": enabled}
    assert harness.public_status(owner)["pluginChange"]["enabled"] is enabled
    restarts = owner.bridge.log.count("restart")
    monkeypatch.setattr(c, "_write_record", lambda *_: pytest.fail("settled record was written twice"))
    assert turn(owner, enabled) == {"outcome": "applied"}
    assert pending(owner) is None
    assert turn(owner, enabled) == {"outcome": "unchanged"}
    assert owner.bridge.log.count("restart") == restarts


def test_failed_recovery_keeps_actionable_pending_then_recovers(owner, monkeypatch):
    crash_before_settlement(owner, monkeypatch, True)
    with monkeypatch.context() as fault:
        def refuse(*_args):
            raise AccessError("connection-runtime-proof-failed")
        fault.setattr(c, "_verify", refuse)
        with pytest.raises(AccessError, match="connection-runtime-proof-failed"):
            turn(owner, True)
    assert harness.record(owner) is None
    assert harness.public_status(owner)["pluginChange"] == {
        "plugin": "memory-wiki", "enabled": True, "error": "connection-runtime-proof-failed"}
    with pytest.raises(AccessError, match="connection-transition-in-progress"):
        harness.begin(owner)
    assert turn(owner, True) == {"outcome": "applied"}
    assert pending(owner) is None


def test_record_drift_is_not_clobbered_or_hidden(owner, monkeypatch):
    crash_before_settlement(owner, monkeypatch, True)
    drifted = {"schemaVersion": state.SCHEMA_VERSION, "kind": state.KIND,
               "updatedAt": "2026-10-30T00:00:00Z", "ownerTimeZone": "UTC", "connectors": {}}
    c._write_record(state.validate(drifted))
    with pytest.raises(AccessError, match="connection-record-changed"):
        turn(owner, True)
    assert harness.record(owner) == drifted
    assert harness.public_status(owner)["pluginChange"]["error"] == "connection-record-changed"
    assert pending(owner) is not None


def test_rollback_interrupted_before_release_keeps_old_choice(owner, monkeypatch):
    monkeypatch.setattr(harness.tx, "_catalog_ready", lambda _openclaw, chosen: not chosen)
    old_config = live(owner)
    crash_before_settlement(owner, monkeypatch, True)
    assert pending(owner)["outcome"] == "rolled-back"
    assert live(owner) == old_config and harness.record(owner) is None
    with pytest.raises(AccessError, match="plugin-change-rolled-back"):
        turn(owner, True)
    assert pending(owner) is None
    assert live(owner) == old_config and harness.record(owner) is None


def test_other_connection_and_access_writers_do_not_consume_plugin_journal(owner, monkeypatch):
    crash_before_settlement(owner, monkeypatch, True)
    before = journal_path(owner).read_bytes()
    for action in (lambda: turn(owner, False), lambda: harness.begin(owner),
                   lambda: c.control(owner.bridge, "connection-disconnect", {"connector": "google"})):
        with pytest.raises(AccessError, match="connection-transition-in-progress"):
            action()
        assert journal_path(owner).read_bytes() == before
    snapshot = owner.bridge.inspect()
    with pytest.raises(AccessError, match="settings-recovery-required"):
        owner.bridge.change({"mode": "full-access", "confirmed": True, "revision": snapshot["revision"]})
    assert journal_path(owner).read_bytes() == before
    assert turn(owner, True) == {"outcome": "applied"}


def observe_journals(monkeypatch):
    observed, original = [], c._write
    def write(bridge, journal):
        observed.append(json.loads(json.dumps(journal)))
        return original(bridge, journal)
    monkeypatch.setattr(c, "_write", write)
    return observed


def test_recorded_choice_with_legacy_live_mismatch_is_journaled_and_repaired(owner, monkeypatch):
    old_config = live(owner)
    old_answers = json.loads(owner.bridge.answers_path.read_text())
    turn(owner, True)
    record = harness.record(owner)
    harness.atomic_json(owner.bridge.config_path, old_config)
    harness.atomic_json(owner.bridge.answers_path, old_answers)
    observed = observe_journals(monkeypatch)
    restarts = owner.bridge.log.count("restart")
    assert turn(owner, True) == {"outcome": "applied"}
    assert harness.record(owner) == record
    assert plugins.enabled_in(live(owner)) == ["memory-wiki"]
    assert owner.bridge.log.count("restart") == restarts + 1
    change = observed[0]["pluginChange"]
    assert change["beforeRecordSha"] == change["afterRecordSha"]


def test_live_target_already_matches_but_record_change_requires_real_owner_completion(owner, monkeypatch):
    turn(owner, True)
    config, answers = live(owner), json.loads(owner.bridge.answers_path.read_text())
    c._write_record(None)
    observed = observe_journals(monkeypatch)
    restarts = owner.bridge.log.count("restart")
    assert turn(owner, True) == {"outcome": "applied"}
    assert live(owner) == config and json.loads(owner.bridge.answers_path.read_text()) == answers
    assert harness.record(owner)["plugins"] == {"memory-wiki": True}
    assert owner.bridge.log.count("restart") == restarts + 1
    change = observed[0]["pluginChange"]
    assert change["beforeRecordSha"] != change["afterRecordSha"]
    receipt = owner.bridge.worker("connection-owner-status")["completion"]
    assert receipt["transactionId"] == observed[0]["transactionId"] and receipt["outcome"] == "applied"
    assert pending(owner) is None


@pytest.mark.parametrize("corruption", ["hash", "target", "error"])
def test_bad_private_journal_remains_unknown_pending_without_leaking(owner, monkeypatch, corruption):
    crash_before_settlement(owner, monkeypatch, True)
    raw = pending(owner)
    if corruption == "hash":
        raw["pluginChange"]["beforeRecordSha"] = "not-a-hash"
    elif corruption == "target":
        raw["pluginChange"]["enabled"] = False
    else:
        raw["error"] = "private text must not reach UI"
    harness.atomic_json(journal_path(owner), raw)
    public = harness.public_status(owner)
    assert public["pluginChange"] == {"plugin": None, "enabled": None, "error": "connection-transition-unavailable"}
    assert "RecordSha" not in json.dumps(public) and "private text" not in json.dumps(public)
    public["pluginChange"]["beforeRecordSha"] = "a" * 64
    with pytest.raises(status.StatusError):
        status.validate_public(public)
