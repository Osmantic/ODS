"""Calendar changes Portal suggests, approved in the dashboard, end to end.

Root's snapshot custody and the owner's hash check run for real, and the real
Source Broker makes each approved change against a fake Google Calendar.
systemd and the gateway are simulated as in test_connection_coordinator.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

from datetime import datetime, timezone
import hashlib
import io
import json
import os
import stat
import urllib.error
import urllib.parse

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import approvals, coordinator as c, status

BROKER = harness.BROKER
world = harness.world  # The custodian's fixture: fake systemd, gateway and Google token endpoint.
WRITE = dict(harness.READ_BOTH, **{"calendar.write": True})
CALENDAR_WRITE = "https://www.googleapis.com/auth/calendar.events"
START, END = "2026-10-27T15:00:00-05:00", "2026-10-27T16:00:00-05:00"
DENTIST = {"summary": "Dentist", "start": START, "end": END, "timeZone": "America/Chicago", "location": "12 Main St",
           "attendees": [{"email": "pat@example.com"}], "sendUpdates": "all"}


class FakeCalendar(harness.FakeGoogle):
    """Google's token endpoint and one calendar's events, as the broker's urlopen reaches them."""

    def __init__(self):
        super().__init__()
        self.token = harness.granted(harness.GMAIL, harness.CALENDAR, CALENDAR_WRITE)
        self.events = {}
        self.writes = []
        self.lose_reply = False

    def __call__(self, request, timeout=None):
        if not request.full_url.startswith(BROKER.CALENDAR_API):
            return super().__call__(request, timeout)
        method, parts = request.get_method(), urllib.parse.urlsplit(request.full_url)
        event_id = urllib.parse.unquote(parts.path.split("/events/", 1)[1]) if "/events/" in parts.path else None
        body = json.loads(request.data) if request.data else None
        if method == "GET":
            if event_id not in self.events:
                self.refuse(request, 404, "notFound")
            return io.BytesIO(json.dumps(self.events[event_id]).encode())
        self.writes.append((method, event_id, body, urllib.parse.parse_qs(parts.query)))
        if method == "POST":
            event_id = body["id"]
            self.events[event_id] = dict(body, etag='"e1"', status="confirmed")
        else:
            if event_id not in self.events:
                self.refuse(request, 404, "notFound")
            if request.get_header("If-match") != self.events[event_id]["etag"]:
                self.refuse(request, 412, "conditionNotMet")
            if method == "PATCH":
                self.events[event_id] = dict(self.events[event_id], **body, etag='"e2"')
            else:
                del self.events[event_id]
        if self.lose_reply:
            # Google made the change, but its answer never arrived.
            raise urllib.error.URLError("fixture: reply lost")
        return io.BytesIO(json.dumps(self.events.get(event_id, {})).encode() if method != "DELETE" else b"")

    @staticmethod
    def refuse(request, code, reason):
        raw = json.dumps({"error": {"code": code, "errors": [{"reason": reason}]}}).encode()
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, io.BytesIO(raw))


def unit(function, name):
    """A oneshot unit as systemd runs it: the broker's exit status, or failure when it raises."""
    try:
        return function(name.split("@", 1)[1].removesuffix(".service"))
    except Exception:  # noqa: BLE001 - the unit's process boundary; the result files decide
        return 1


@pytest.fixture
def owner(world, monkeypatch):
    """A connected Google account with calendar changes on, and the broker's action units."""
    return connected(world, monkeypatch, WRITE)


def connected(world, monkeypatch, capabilities):
    """Connect Google with these capabilities against a fake calendar, with the broker's action units."""
    calendar = FakeCalendar()
    monkeypatch.setattr(BROKER.urllib.request, "urlopen", calendar)
    world.google = calendar
    for name, value in (("PIXEL_ACTION_RESULT_DIR", world.paths.results),
                        ("PIXEL_ACTION_PROPOSAL_DIR", world.paths.proposals), ("PIXEL_SOURCE_CALENDAR_ID", "primary"),
                        ("PIXEL_LIMB_CALENDAR_ENABLED", "1"), ("PIXEL_LIMB_CALENDAR_WRITE_ENABLED", "1")):
        monkeypatch.setenv(name, str(value))
    run = c.RUN

    def systemd(arguments, timeout):
        name = arguments[-1]
        if arguments[:2] == ["systemctl", "start"] and name.startswith("pixel-source-action@"):
            world.commands.append(arguments)
            return unit(BROKER.approve, name)
        if arguments[:2] == ["systemctl", "start"] and name.startswith("pixel-source-reconcile@"):
            world.commands.append(arguments)
            return unit(BROKER.reconcile, name)
        return run(arguments, timeout)
    monkeypatch.setattr(c, "RUN", systemd)
    flow = harness.begin(world, capabilities=capabilities)
    reply = c.control(world.bridge, "connection-complete", {
        "flowId": flow["flowId"], "state": harness.consent_state(world, flow["flowId"]), "code": "4/fixture-code"})
    assert reply["capabilities"] == capabilities
    world.writes = calendar.writes
    assert world.writes == [], 'connecting/quiescing must not write a calendar change'
    # Keep setup evidence in commands (including the action/reconcile stops).
    # Approval execution assertions start after the connected fixture is ready.
    world.approval_command_baseline = len(world.commands)
    return world


def propose(world, action, values, *, status="pending-operator-approval", stamp=1761500000000):
    """A proposal exactly as Portal's pixel_calendar_propose_* tools write it."""
    proposal_id = f"calendar-{stamp + len(list(world.paths.proposals.iterdir()))}-{os.urandom(4).hex()}"
    path = world.paths.proposals / f"{proposal_id}.json"
    path.write_text(json.dumps({
        "schemaVersion": 1, "proposalId": proposal_id, "source": "pixel-owner-conversation", "action": action,
        "status": status, "createdAt": "2026-10-26T15:00:00.000Z", "values": values,
        "boundary": "This file is a proposal only. No Calendar mutation has occurred."}, indent=2) + "\n")
    path.chmod(0o640)
    return proposal_id


def listing(world):
    return c.control(world.bridge, "connection-proposals", {})


def only(world):
    (entry,) = listing(world)["proposals"]
    return entry


def approve(world, entry, sha256=None):
    return c.control(world.bridge, "connection-approve",
                     {"proposalId": entry["proposalId"], "sha256": sha256 or entry["sha256"]})


def actions(world):
    """Actual action/reconcile executions in this scenario, after connection setup."""
    return [command for command in world.commands[world.approval_command_baseline:]
            if command[:2] == ['systemctl', 'start']
            and command[-1].startswith(("pixel-source-action@", "pixel-source-reconcile@"))]


def calendar_summary(world, *records):
    path = world.paths.projection / "calendar.json"
    path.write_text(json.dumps({"schemaVersion": 1, "source": "calendar", "records": list(records)}))
    path.chmod(0o640)


def test_an_approved_change_is_made_once_exactly_as_the_owner_saw_it(owner):
    proposal_id = propose(owner, "create", DENTIST)
    source = owner.paths.proposals / f"{proposal_id}.json"
    shown = only(owner)
    snapshot = owner.paths.results / f"{proposal_id}.approved.json"
    assert shown == {"proposalId": proposal_id, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "state": "waiting", "action": "create", "createdAt": "2026-10-26T15:00:00.000Z",
                     "change": {"summary": "Dentist", "start": START, "end": END, "allDay": False,
                                "timeZone": "America/Chicago", "location": "12 Main St", "description": None,
                                "attendees": ["pat@example.com"], "sendUpdates": "all"},
                     "event": None, "tooLarge": False}
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o640 and snapshot.read_bytes() == source.read_bytes()

    # Portal rewriting its file afterwards changes nothing the owner approves.
    source.write_text(source.read_text().replace("Dentist", "Something else"))
    assert only(owner) == shown

    assert approve(owner, shown) == {"proposalId": proposal_id, "outcome": "applied"}
    ((method, _event, body, query),) = owner.writes
    assert method == "POST" and query == {"sendUpdates": ["all"]}
    assert body["summary"] == "Dentist" and body["attendees"] == [{"email": "pat@example.com"}]
    assert body["start"] == {"dateTime": START, "timeZone": "America/Chicago"}
    assert not source.exists(), "a settled change leaves the owner's list"
    assert ["systemctl", "start", "--no-block", "pixel-source-broker.service"] in owner.commands

    # A repeated approval, say after a lost reply, reports the first outcome and sends nothing.
    assert approve(owner, shown) == {"proposalId": proposal_id, "outcome": "applied"}
    assert len(owner.writes) == 1 and len(actions(owner)) == 1
    assert listing(owner) == {"proposals": [], "more": 0}


def test_an_approval_for_anything_but_the_snapshot_changes_nothing(owner):
    propose(owner, "create", DENTIST)
    shown = only(owner)
    with pytest.raises(AccessError, match="calendar-change-mismatch"):
        approve(owner, shown, sha256="0" * 64)
    with pytest.raises(AccessError, match="invalid-connection-request"):
        approve(owner, shown, sha256="not-a-hash")
    with pytest.raises(AccessError, match="calendar-change-unknown"):
        approve(owner, dict(shown, proposalId="calendar-1761500000099-0badc0de"))
    assert owner.writes == [] and actions(owner) == [] and only(owner)["state"] == "waiting"


def test_approving_needs_calendar_changes_on_and_the_sign_in(world, monkeypatch):
    harness.connect(world)  # Read-only: calendar changes stay off.
    propose(world, "create", DENTIST)
    shown = only(world)
    with pytest.raises(AccessError, match="calendar-changes-off"):
        approve(world, shown)
    world.paths.token.unlink()
    with pytest.raises(AccessError, match="connection-not-connected"):
        approve(world, shown)
    # The owner can still clear it away.
    assert c.control(world.bridge, "connection-decline", {"proposalId": shown["proposalId"]})["outcome"] == "declined"
    assert listing(world)["proposals"] == []


def test_a_change_google_refuses_is_settled_and_never_retried(owner):
    calendar_summary(owner, {"id": "evt-1", "etag": '"e1"', "title": "Team sync",
                             "start": {"dateTime": "2026-10-27T10:00:00-05:00"},
                             "end": {"dateTime": "2026-10-27T10:30:00-05:00"}})
    owner.google.events["evt-1"] = {"id": "evt-1", "etag": '"e9"', "summary": "Team sync", "status": "confirmed"}
    propose(owner, "update", {"eventId": "evt-1", "expectedEtag": '"e1"', "start": START, "end": END})
    shown = only(owner)
    assert shown["event"] == {"title": "Team sync", "start": "2026-10-27T10:00:00-05:00",
                              "end": "2026-10-27T10:30:00-05:00", "allDay": False}
    assert shown["change"]["summary"] is None and shown["change"]["attendees"] is None, "None keeps what the event has"
    assert approve(owner, shown)["outcome"] == "rejected"
    assert owner.google.events["evt-1"]["etag"] == '"e9"' and listing(owner)["proposals"] == []


def test_an_unconfirmed_change_is_never_sent_twice_and_a_new_event_can_be_checked(owner):
    owner.google.lose_reply = True
    propose(owner, "create", dict(DENTIST, sendUpdates="none", attendees=[]))
    shown = only(owner)
    assert approve(owner, shown)["outcome"] == "unconfirmed"
    assert only(owner)["state"] == "unconfirmed"
    with pytest.raises(AccessError, match="calendar-change-unconfirmed"):
        approve(owner, shown)
    assert len(owner.writes) == 1 and len(actions(owner)) == 1
    owner.google.lose_reply = False
    reply = c.control(owner.bridge, "connection-reconcile", {"proposalId": shown["proposalId"]})
    assert reply == {"proposalId": shown["proposalId"], "outcome": "applied"}
    assert len(owner.writes) == 1, "checking reads Google; it never writes"
    assert listing(owner)["proposals"] == []


def test_an_unconfirmed_change_to_an_existing_event_is_only_dismissed(owner):
    owner.google.events["evt-2"] = {"id": "evt-2", "etag": '"e1"', "summary": "Lunch", "status": "confirmed"}
    owner.google.lose_reply = True
    proposal_id = propose(owner, "delete", {"eventId": "evt-2", "expectedEtag": '"e1"'})
    assert approve(owner, only(owner))["outcome"] == "unconfirmed"
    with pytest.raises(AccessError, match="calendar-change-not-checkable"):
        c.control(owner.bridge, "connection-reconcile", {"proposalId": proposal_id})
    assert c.control(owner.bridge, "connection-decline", {"proposalId": proposal_id})["outcome"] == "declined"
    assert listing(owner)["proposals"] == []
    # Evidence that Google may have it stays, so nothing can ever send it again.
    assert (owner.paths.results / f"{proposal_id}.approved.json").exists()
    assert (owner.paths.results / f"{proposal_id}.processing.json").exists()


def test_a_change_that_never_reached_google_can_be_approved_again(owner):
    propose(owner, "create", DENTIST)
    shown = only(owner)
    working = owner.google.token
    owner.google.token = lambda body: (503, {"error": "backend"})
    with pytest.raises(AccessError, match="calendar-change-not-made"):
        approve(owner, shown)
    assert owner.writes == [] and only(owner)["state"] == "waiting"
    owner.google.token = working
    assert approve(owner, shown)["outcome"] == "applied" and len(owner.writes) == 1


def test_declining_removes_the_suggestion_and_its_frozen_copy(owner):
    proposal_id = propose(owner, "create", DENTIST)
    only(owner)
    assert c.control(owner.bridge, "connection-decline", {"proposalId": proposal_id}) == {
        "proposalId": proposal_id, "outcome": "declined"}
    assert not (owner.paths.proposals / f"{proposal_id}.json").exists()
    assert not (owner.paths.results / f"{proposal_id}.approved.json").exists()
    assert listing(owner)["proposals"] == [] and owner.writes == []


def test_only_changes_waiting_for_the_owner_are_listed_and_unreadable_ones_only_declined(owner):
    direct = propose(owner, "create", dict(DENTIST, attendees=[], sendUpdates="none"), status="pending-bounded-direct")
    broken = propose(owner, "create", DENTIST)
    (owner.paths.proposals / f"{broken}.json").write_text("{not json")
    placeholder = propose(owner, "update", {"eventId": "evt-1", "expectedEtag": '"e1"',
                                            "summary": "[quarantined event title]"})
    elsewhere = owner.paths.state / "elsewhere.json"
    elsewhere.write_text("{}")
    linked = f"calendar-1761500000090-{os.urandom(4).hex()}"
    (owner.paths.proposals / f"{linked}.json").symlink_to(elsewhere)
    entries = {entry["proposalId"]: entry for entry in listing(owner)["proposals"]}
    assert direct not in entries, "Pixel makes bounded direct changes itself"
    assert not (owner.paths.results / f"{direct}.approved.json").exists()
    for proposal_id in (broken, placeholder, linked):
        assert entries[proposal_id] == {"proposalId": proposal_id, "sha256": None, "state": "invalid", "action": None,
                                        "createdAt": None, "change": None, "event": None, "tooLarge": False}
    assert not (owner.paths.results / f"{broken}.approved.json").exists()
    for proposal_id in (broken, linked):
        c.control(owner.bridge, "connection-decline", {"proposalId": proposal_id})
    assert elsewhere.exists(), "declining removes the link, never what it points at"
    assert set(entry["proposalId"] for entry in listing(owner)["proposals"]) == {placeholder}


def test_a_change_too_long_to_show_whole_cannot_be_approved_here(owner):
    proposal_id = propose(owner, "create", dict(DENTIST, description="é" * 20_000))
    entry = only(owner)
    assert entry["tooLarge"] is True and entry["sha256"] is None and entry["change"] is None
    assert entry["state"] == "waiting" and entry["action"] == "create"
    c.control(owner.bridge, "connection-decline", {"proposalId": proposal_id})
    assert listing(owner)["proposals"] == []


def test_the_list_fits_every_hop_and_says_how_many_more_wait(owner):
    for _ in range(5):
        propose(owner, "create", dict(DENTIST, description="n" * 14_000))
    value = listing(owner)
    assert len(value["proposals"]) == 3 and value["more"] == 2
    assert len(json.dumps({"status": 200, "body": value})) < 65536
    assert [entry["proposalId"] for entry in value["proposals"]] == sorted(entry["proposalId"]
                                                                          for entry in value["proposals"])


def test_a_snapshot_interrupted_before_its_link_was_removed_heals(owner):
    proposal_id = propose(owner, "create", DENTIST)
    only(owner)
    snapshot = owner.paths.results / f"{proposal_id}.approved.json"
    os.link(snapshot, owner.paths.results / f".{proposal_id}.snapshot.k9x2")
    assert snapshot.stat().st_nlink == 2
    assert only(owner)["state"] == "waiting" and snapshot.stat().st_nlink == 1


def test_status_counts_waiting_changes_and_never_their_content(owner):
    propose(owner, "create", DENTIST)
    owner.google.lose_reply = True
    propose(owner, "create", dict(DENTIST, summary="Haircut", attendees=[], sendUpdates="none"))
    first, second = listing(owner)["proposals"]
    assert approve(owner, second)["outcome"] == "unconfirmed"
    propose(owner, "create", dict(DENTIST, attendees=[], sendUpdates="none"), status="pending-bounded-direct")
    assert approvals.pending_count(owner.paths) == 2
    report = status.report(surface="linux-systemd", record_path=owner.record, token_path=owner.paths.token,
                           status_path=owner.paths.projection / "status.json",
                           lifecycle_path=owner.paths.state / "missing.json", owners=(0, os.getuid()),
                           broker_state=owner.paths.state, now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert status.validate_public(report)["connectors"]["google"]["waitingChanges"] == 2
    assert "Dentist" not in json.dumps(report) and "Haircut" not in json.dumps(report)
    owner.google.lose_reply = False
    c.control(owner.bridge, "connection-decline", {"proposalId": first["proposalId"]})
    assert approvals.pending_count(owner.paths) == 1


def test_validation_matches_the_broker_on_the_proposals_portal_writes():
    base = {"schemaVersion": 1, "proposalId": "calendar-1761500000000-0badc0de", "source": "pixel-owner-conversation",
            "status": "pending-operator-approval", "createdAt": "2026-10-26T15:00:00.000Z",
            "boundary": "This file is a proposal only. No Calendar mutation has occurred."}
    cases = [
        ("create", DENTIST), ("create", dict(DENTIST, allDay=True, start="2026-10-27", end="2026-10-28")),
        ("update", {"eventId": "evt-1", "expectedEtag": '"e1"', "attendees": []}),
        ("update", {"eventId": "evt-1", "expectedEtag": 'W/"e1"', "description": ""}),
        ("delete", {"eventId": "evt-1", "expectedEtag": '"e1"', "sendUpdates": "all"}),
        ("create", dict(DENTIST, summary=" ")), ("create", dict(DENTIST, end=START)),
        ("create", dict(DENTIST, start="2026-10-27T15:00:00")), ("create", dict(DENTIST, sendUpdates="some")),
        ("create", dict(DENTIST, attendees=[{"email": "pat@example.com"}, {"email": "PAT@example.com"}])),
        ("create", dict(DENTIST, attendees=[{"email": "pat@example.com", "optional": True}])),
        ("update", {"eventId": "evt-1", "expectedEtag": '"e1"'}), ("update", {"eventId": "evt-1", "summary": "x"}),
        ("delete", {"eventId": "evt-1", "expectedEtag": '"e1"', "summary": "x"}),
        ("create", dict(DENTIST, location="Room\x07")), ("create", dict(DENTIST, timeZone="Mars/../Base")),
        ("move", DENTIST), (["create"], DENTIST),
    ]
    for action, values in cases:
        value = dict(base, action=action, values=values)
        try:
            BROKER.validate_proposal(json.loads(json.dumps(value)), base["proposalId"])
            expected = True
        except (RuntimeError, TypeError):
            expected = False
        try:
            approvals.validate(json.loads(json.dumps(value)), base["proposalId"])
            ours = True
        except ValueError:
            ours = False
        assert ours == expected, (action, values)
