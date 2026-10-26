"""Undoing a private calendar change Portal made by itself, end to end.

The real Source Broker makes Portal's private change and then the owner's
reversal against a fake Google Calendar; root lists what Portal did from the
broker's own results and builds each reversal from them. systemd and the
gateway are simulated as in test_connection_coordinator.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

from datetime import datetime, timedelta, timezone
import json

import pytest

import test_connection_approvals as calendar_harness
import test_connection_coordinator as harness
import test_connection_direct as direct_harness
from pixel_access_bridge import AccessError
from pixel_connections import activity, coordinator as c

world = harness.world  # The custodian's fixture: fake systemd, gateway and Google token endpoint.
owner = direct_harness.owner  # Google connected with private changes on.
START, END = calendar_harness.START, calendar_harness.END
LATER = {"start": "2026-10-28T15:00:00-05:00", "end": "2026-10-28T16:00:00-05:00"}


def stamp():
    """Now in milliseconds, as Portal's tools stamp a proposal's ID; what Portal did is listed for a week from it."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def summary(world, *events, at=None):
    """The broker's calendar summary as its refresh writes it, made after the changes so far by default."""
    records = [{"id": event["id"], "etag": event["etag"], "status": "confirmed", "title": event.get("summary"),
                "start": event["start"], "end": event["end"], "attendees": event.get("attendees", [])}
               for event in events]
    generated = at or (datetime.now(timezone.utc) + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = world.paths.projection / "calendar.json"
    path.write_text(json.dumps({"schemaVersion": 1, "source": "calendar", "generatedAt": generated,
                                "records": records}))
    path.chmod(0o640)


def listed(world):
    return c.control(world.bridge, "connection-activity", {})


def undo(world, proposal_id):
    return c.control(world.bridge, "connection-undo", {"proposalId": proposal_id})


def made(world, action, values):
    """One private change Portal queued just now and the broker made, as its path unit would run it."""
    proposal_id = calendar_harness.propose(world, action, values, status=direct_harness.DIRECT_STATUS, stamp=stamp())
    direct_harness.drain(world)
    assert direct_harness.result(world, proposal_id)["status"] == "applied"
    return proposal_id


def test_an_event_portal_added_is_removed_by_undo_once(owner):
    proposal_id = made(owner, "create", direct_harness.PRIVATE)
    created = direct_harness.result(owner, proposal_id)["affectedEventId"]
    summary(owner, owner.google.events[created])
    (entry,) = listed(owner)["changes"]
    assert entry == {"proposalId": proposal_id, "action": "create", "title": "Focus time", "start": START, "end": END,
                     "allDay": False, "previous": None, "appliedAt": entry["appliedAt"], "undo": "available"}
    assert undo(owner, proposal_id) == {"proposalId": proposal_id, "outcome": "applied"}
    method, event_id, _body, query = owner.writes[-1]
    assert (method, event_id, query["sendUpdates"]) == ("DELETE", created, ["none"])
    assert created not in owner.google.events
    assert listed(owner)["changes"][0]["undo"] == "done"
    # Undoing again reports the first reversal and sends nothing.
    writes = len(owner.writes)
    assert undo(owner, proposal_id)["outcome"] == "applied" and len(owner.writes) == writes


def test_an_event_portal_moved_moves_back_to_where_it_was(owner):
    owner.google.events["evt-4"] = {"id": "evt-4", "etag": '"e1"', "summary": "Gym", "status": "confirmed",
                                   "start": {"dateTime": START}, "end": {"dateTime": END}}
    proposal_id = made(owner, "update", dict(LATER, eventId="evt-4", expectedEtag='"e1"'))
    summary(owner, owner.google.events["evt-4"])
    (entry,) = listed(owner)["changes"]
    assert (entry["action"], entry["title"], entry["start"], entry["undo"]) == ("update", "Gym", LATER["start"],
                                                                                "available")
    assert entry["previous"] == {"start": START, "end": END, "allDay": False}
    assert undo(owner, proposal_id)["outcome"] == "applied"
    method, event_id, body, _query = owner.writes[-1]
    assert (method, event_id) == ("PATCH", "evt-4")
    assert body["start"] == {"dateTime": START} and "summary" not in body
    assert owner.google.events["evt-4"]["start"] == {"dateTime": START}


def test_an_event_changed_since_is_never_undone(owner):
    proposal_id = made(owner, "create", direct_harness.PRIVATE)
    created = direct_harness.result(owner, proposal_id)["affectedEventId"]
    shared = dict(owner.google.events[created], attendees=[{"email": "pat@example.com"}])
    summary(owner, shared)
    assert listed(owner)["changes"][0]["undo"] == "changed", "deleting it now would reach a guest"
    summary(owner, dict(owner.google.events[created], start={"dateTime": LATER["start"]},
                        end={"dateTime": LATER["end"]}))
    assert listed(owner)["changes"][0]["undo"] == "changed"
    writes = len(owner.writes)
    with pytest.raises(AccessError, match="calendar-undo-unavailable"):
        undo(owner, proposal_id)
    assert len(owner.writes) == writes


def test_a_summary_older_than_the_change_waits_for_the_next_sync(owner):
    proposal_id = made(owner, "create", direct_harness.PRIVATE)
    summary(owner, at="2026-01-01T00:00:00Z")
    assert listed(owner)["changes"][0]["undo"] == "syncing"
    summary(owner)
    assert listed(owner)["changes"][0]["undo"] == "gone"
    with pytest.raises(AccessError, match="calendar-undo-unavailable"):
        undo(owner, proposal_id)


def test_google_refusing_an_undo_settles_it(owner):
    proposal_id = made(owner, "create", direct_harness.PRIVATE)
    created = direct_harness.result(owner, proposal_id)["affectedEventId"]
    summary(owner, owner.google.events[created])
    owner.google.events[created]["etag"] = '"e7"'  # Changed at Google after the last sync.
    assert undo(owner, proposal_id)["outcome"] == "rejected"
    assert created in owner.google.events
    assert listed(owner)["changes"][0]["undo"] == "refused"


def test_only_this_weeks_changes_portal_made_by_itself_are_listed(owner):
    calendar_harness.propose(owner, "create", calendar_harness.DENTIST, stamp=stamp())
    assert calendar_harness.approve(owner, calendar_harness.only(owner))["outcome"] == "applied"
    mine = made(owner, "create", direct_harness.PRIVATE)
    summary(owner)
    assert [entry["proposalId"] for entry in listed(owner)["changes"]] == [mine], "the owner approved the other"
    ids, ledger = c._approval_ids(owner.bridge), owner.bridge.state / "calendar-undo.json"
    made_at = int(mine.split("-")[1]) / 1000
    for days, expected in ((6, [mine]), (8, [])):
        later = datetime.fromtimestamp(made_at, timezone.utc) + timedelta(days=days)
        assert [entry["proposalId"] for entry in activity.listing(owner.paths, ids, ledger, now=later)["changes"]] \
            == expected


def test_undo_needs_calendar_changes_on_and_the_sign_in(owner):
    proposal_id = made(owner, "create", direct_harness.PRIVATE)
    owner.paths.token.unlink()
    with pytest.raises(AccessError, match="connection-not-connected"):
        undo(owner, proposal_id)
    with pytest.raises(AccessError, match="invalid-connection-request"):
        c.control(owner.bridge, "connection-undo", {"proposalId": "calendar-x"})
