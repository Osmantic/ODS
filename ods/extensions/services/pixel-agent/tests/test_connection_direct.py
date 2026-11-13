"""Private calendar changes Portal makes by itself: bounded, opt-in, and paused while Portal has Full Access.

The real Source Broker drains Portal's queued changes against a fake Google
Calendar, as its path unit does; the custodian pauses and resumes it around
access-mode changes. systemd and the gateway are simulated as in
test_connection_coordinator.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

import json
import os

import pytest

import test_connection_approvals as calendar_harness
import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, custody, lifecycle, planner, state

BROKER = harness.BROKER
world = harness.world  # The custodian's fixture: fake systemd, gateway and Google token endpoint.
DIRECT = dict(harness.READ_BOTH, **{"calendar.write": True, "calendar.direct": True})
START, END = calendar_harness.START, calendar_harness.END
PRIVATE = {"summary": "Focus time", "start": START, "end": END, "timeZone": "America/Chicago"}
DIRECT_STATUS = "pending-bounded-direct"


@pytest.fixture
def owner(world, monkeypatch):
    """Google connected with private changes on, and Portal's gateway rendered for it."""
    calendar_harness.connected(world, monkeypatch, DIRECT)
    world.monkeypatch = monkeypatch
    return world


def flags(world):
    """Whether the broker may run queued private changes, and whether Portal's gateway queues them."""
    broker = dict(custody.parse_env(world.paths.env.read_text()))[custody.DIRECT_FLAG] == "1"
    return broker, planner.gateway_limbs(world.bridge.env_path.read_text())["calendarDirect"]


def watching(world):
    toggles = [command[1] for command in world.commands if command[-1] == "pixel-source-direct.path"]
    return bool(toggles) and toggles[-1] == "enable"


def drain(world):
    """The direct path unit: when it watches, the broker drains the queue with its installed flags."""
    if not watching(world):
        return None
    installed = dict(custody.parse_env(world.paths.env.read_text()))
    for key in (custody.DIRECT_FLAG, "PIXEL_LIMB_CALENDAR_ENABLED", "PIXEL_LIMB_CALENDAR_WRITE_ENABLED"):
        world.monkeypatch.setenv(key, installed[key])
    try:
        return BROKER.drain_direct()
    except Exception:  # noqa: BLE001 - the unit's process boundary; the result files decide
        return 1


def queue(world, action, values):
    return calendar_harness.propose(world, action, values, status=DIRECT_STATUS)


def result(world, proposal_id):
    path = world.paths.results / f"{proposal_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def record(world):
    return state.read(world.record, owners=(0, os.getuid()))


def test_only_private_creates_and_time_only_moves_apply_by_themselves(owner):
    assert flags(owner) == (True, True) and watching(owner)
    focus = queue(owner, "create", PRIVATE)
    invite = queue(owner, "create", dict(PRIVATE, attendees=[{"email": "pat@example.com"}]))
    drain(owner)
    assert result(owner, focus)["status"] == "applied"
    assert result(owner, focus)["approvalBinding"] == "bounded-direct-policy"
    assert result(owner, invite) is None, "a change with guests always waits for the owner"
    ((method, _event, body, _query),) = owner.writes
    assert method == "POST" and body["summary"] == "Focus time"

    owner.google.events["evt-3"] = {"id": "evt-3", "etag": '"e1"', "summary": "Gym", "status": "confirmed",
                                   "start": {"dateTime": START}, "end": {"dateTime": END}}
    later = {"start": "2026-10-28T15:00:00-05:00", "end": "2026-10-28T16:00:00-05:00"}
    move = queue(owner, "update", dict(later, eventId="evt-3", expectedEtag='"e1"'))
    rename = queue(owner, "update", dict(later, eventId="evt-3", expectedEtag='"e1"', summary="Gym, legs"))
    drain(owner)
    moved = result(owner, move)
    assert moved["status"] == "applied" and moved["rollback"]["start"] == {"dateTime": START}
    assert result(owner, rename) is None, "anything beyond a new time waits for the owner"
    assert owner.google.events["evt-3"]["summary"] == "Gym"


def test_twenty_an_hour_and_the_rest_are_not_made(owner):
    queued = [queue(owner, "create", dict(PRIVATE, summary=f"Block {index}")) for index in range(21)]
    drain(owner)
    made = [proposal_id for proposal_id in queued if result(owner, proposal_id)]
    assert len(made) == 20 and len(owner.writes) == 20
    drain(owner)
    assert len(owner.writes) == 20


def test_full_access_pauses_private_changes_and_sandbox_resumes_them_without_late_changes(owner):
    c.pause_for_full_access(owner.bridge)
    assert record(owner)["connectors"]["google"]["directPaused"] is True
    assert flags(owner) == (False, True) and not watching(owner)
    owner.bridge.mode = "full-access"  # The access-mode change itself, done by Portal's owner worker.

    # Portal's gateway still queues until it catches up; the broker never makes those.
    queued = queue(owner, "create", PRIVATE)
    owner.monkeypatch.setenv(custody.DIRECT_FLAG, "0")
    with pytest.raises(RuntimeError):
        BROKER.drain_direct()
    assert owner.writes == [] and result(owner, queued) is None

    assert c.follow_access_mode(owner.bridge) is True
    journal = lifecycle.read(owner.bridge.state / "connection.json", owners=(0, os.getuid()))
    assert journal["phase"] == "activating"
    assert c.control(owner.bridge, "connection-activate", {})["outcome"] == "applied"
    assert flags(owner) == (False, False), "Portal now asks the owner for every change"

    owner.bridge.mode = "sandboxed"
    assert c.follow_access_mode(owner.bridge) is True
    assert c.control(owner.bridge, "connection-activate", {})["outcome"] == "applied"
    assert record(owner)["connectors"]["google"]["directPaused"] is False
    assert flags(owner) == (True, True) and watching(owner)
    assert not (owner.paths.proposals / f"{queued}.json").exists(), "Portal was told it was not made"
    drain(owner)
    assert owner.writes == []


def test_a_catch_up_that_cannot_run_waits_behind_finish(owner):
    c.pause_for_full_access(owner.bridge)
    owner.bridge.mode = "full-access"
    assert c.follow_access_mode(owner.bridge) is True
    owner.bridge.active = True  # Portal is busy with a chat.
    with pytest.raises(AccessError, match="runtime-busy"):
        c.control(owner.bridge, "connection-activate", {})
    journal = lifecycle.read(owner.bridge.state / "connection.json", owners=(0, os.getuid()))
    assert (journal["phase"], journal["error"]) == ("activating", "runtime-busy")
    assert flags(owner)[0] is False, "the broker stays paused while Portal catches up"
    owner.bridge.active = False
    assert c.control(owner.bridge, "connection-activate", {})["outcome"] == "applied"
    assert flags(owner) == (False, False)


def test_connecting_while_portal_has_full_access_starts_paused(world, monkeypatch):
    world.bridge.mode = "full-access"
    calendar_harness.connected(world, monkeypatch, DIRECT)
    assert record(world)["connectors"]["google"]["directPaused"] is True
    assert ["systemctl", "disable", "--now", "pixel-source-direct.path"] in world.commands
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "unchanged"
    assert flags(world) == (False, False)
    # The owner's choice is kept: Sandbox brings it back.
    assert record(world)["connectors"]["google"]["capabilities"]["calendar.direct"] is True


def test_nothing_follows_the_mode_without_private_changes_on(world, monkeypatch):
    calendar_harness.connected(world, monkeypatch, calendar_harness.WRITE)
    assert c.follow_access_mode(world.bridge) is False
    c.pause_for_full_access(world.bridge)
    assert "directPaused" not in record(world)["connectors"]["google"] or \
        record(world)["connectors"]["google"]["directPaused"] is False
    assert flags(world)[0] is False


def test_the_broker_pauses_even_when_the_saved_choices_cannot_be_read(owner):
    owner.record.write_text("{not json")
    c.pause_for_full_access(owner.bridge)
    assert flags(owner)[0] is False and not watching(owner)
    assert c.follow_access_mode(owner.bridge) is False
