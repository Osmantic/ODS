"""Gmail drafts Portal writes, approved in the dashboard, end to end.

Root's snapshot custody and the owner's hash check run for real, and the real
Source Broker creates each approved draft against a fake Gmail that has no way
to send. systemd and the gateway are simulated as in test_connection_coordinator.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

from datetime import datetime, timezone
import base64
import hashlib
import io
import json
import os
import urllib.error
import urllib.parse

import pytest

import test_connection_approvals as calendar_harness
import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, custody, drafts, status

BROKER = harness.BROKER
world = harness.world  # The custodian's fixture: fake systemd, gateway and Google token endpoint.
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
DRAFTS = dict(harness.READ_BOTH, **{"gmail.drafts": True})
LUNCH = {"to": [{"email": "pat@example.com"}], "cc": [{"email": "sam@example.com"}], "subject": "Lunch on Friday",
         "body": "Friday at noon works for me.\n\nSam"}


class FakeGmail(harness.FakeGoogle):
    """Google's token endpoint and one mailbox, as the broker's urlopen reaches them. It cannot send."""

    def __init__(self):
        super().__init__()
        self.token = harness.granted(harness.GMAIL, harness.CALENDAR, COMPOSE)
        self.original = {"id": "orig-1", "threadId": "thread-7", "payload": {"headers": [
            {"name": "Message-ID", "value": "<orig@example.com>"}]}}
        self.drafts = []
        self.requests = []
        self.lose = None  # "request": Gmail never saw it; "reply": Gmail made the draft, its answer was lost.
        self.refuse = False

    def __call__(self, request, timeout=None):
        if not request.full_url.startswith(BROKER.GMAIL_API):
            return super().__call__(request, timeout)
        method, parts = request.get_method(), urllib.parse.urlsplit(request.full_url)
        path = parts.path.removeprefix(urllib.parse.urlsplit(BROKER.GMAIL_API).path)
        self.requests.append((method, path))
        if method == "POST" and path == "/drafts":
            if self.lose == "request":
                raise urllib.error.URLError("fixture: request lost")
            if self.refuse:
                calendar_harness.FakeCalendar.refuse(request, 400, "invalidArgument")
            message = json.loads(request.data)["message"]
            raw = base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4)).decode("utf-8")
            self.drafts.append({"raw": raw, "threadId": message.get("threadId")})
            if self.lose == "reply":
                raise urllib.error.URLError("fixture: reply lost")
            return io.BytesIO(json.dumps({"id": f"r-{len(self.drafts)}",
                                          "message": {"id": f"draft-{len(self.drafts)}"}}).encode())
        if method == "GET" and path == "/messages":
            wanted = "Message-ID: <" + urllib.parse.parse_qs(parts.query)["q"][0].removeprefix("rfc822msgid:") + ">"
            found = [{"id": f"draft-{index}"} for index, draft in enumerate(self.drafts, 1) if wanted in draft["raw"]]
            return io.BytesIO(json.dumps({"messages": found} if found else {}).encode())
        if method == "GET" and path == "/messages/orig-1":
            return io.BytesIO(json.dumps(self.original).encode())
        calendar_harness.FakeCalendar.refuse(request, 404, "notFound")


def connected(world, monkeypatch, capabilities):
    """Connect Google on a Pixel that knows Gmail drafts, with the broker's action units."""
    (world.paths.kit / "source-broker.env").write_text(harness.KIT_ENV + f"{custody.DRAFTS_FLAG}='0'\n")
    gmail = FakeGmail()
    monkeypatch.setattr(BROKER.urllib.request, "urlopen", gmail)
    world.google = gmail
    monkeypatch.setenv("PIXEL_ACTION_RESULT_DIR", str(world.paths.results))
    monkeypatch.setenv("PIXEL_ACTION_PROPOSAL_DIR", str(world.paths.proposals))
    run = c.RUN

    def systemd(arguments, timeout):
        name = arguments[-1]
        if arguments[:2] == ["systemctl", "start"] and name.startswith(("pixel-source-action@",
                                                                         "pixel-source-reconcile@")):
            world.commands.append(arguments)
            # The unit's EnvironmentFile: the broker environment root installed for the record.
            installed = dict(custody.parse_env(world.paths.env.read_text()))
            for key in ("PIXEL_LIMB_EMAIL_ENABLED", custody.DRAFTS_FLAG):
                monkeypatch.setenv(key, installed[key])
            action = BROKER.approve if name.startswith("pixel-source-action@") else BROKER.reconcile
            return calendar_harness.unit(action, name)
        return run(arguments, timeout)
    monkeypatch.setattr(c, "RUN", systemd)
    flow = harness.begin(world, capabilities=capabilities)
    reply = c.control(world.bridge, "connection-complete", {
        "flowId": flow["flowId"], "state": harness.consent_state(world, flow["flowId"]), "code": "4/fixture-code"})
    assert reply["capabilities"] == capabilities
    return world


@pytest.fixture
def owner(world, monkeypatch):
    """A connected Google account with Gmail drafts on."""
    return connected(world, monkeypatch, DRAFTS)


def propose(world, values):
    """A proposal exactly as Pixel's pixel_gmail_draft_propose tool writes it."""
    proposal_id = f"gmail-draft-{1761500000000 + len(list(world.paths.proposals.iterdir()))}-{os.urandom(4).hex()}"
    path = world.paths.proposals / f"{proposal_id}.json"
    path.write_text(json.dumps({
        "schemaVersion": 1, "proposalId": proposal_id, "source": "pixel-owner-conversation", "action": "create-draft",
        "status": "pending-operator-approval", "createdAt": "2026-10-26T15:00:00.000Z", "values": values,
        "boundary": "This file is a proposal only. No Gmail draft has been created."}, indent=2) + "\n")
    path.chmod(0o640)
    return proposal_id


def listing(world):
    return c.control(world.bridge, "connection-drafts", {})


def only(world):
    (entry,) = listing(world)["drafts"]
    return entry


def approve(world, entry, sha256=None):
    return c.control(world.bridge, "connection-approve",
                     {"proposalId": entry["proposalId"], "sha256": sha256 or entry["sha256"]})


def test_an_approved_draft_is_created_once_exactly_as_the_owner_read_it(owner):
    assert dict(custody.parse_env(owner.paths.env.read_text()))[custody.DRAFTS_FLAG] == "1"
    proposal_id = propose(owner, LUNCH)
    source = owner.paths.proposals / f"{proposal_id}.json"
    shown = only(owner)
    assert shown == {"proposalId": proposal_id, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "state": "waiting", "createdAt": "2026-10-26T15:00:00.000Z", "tooLarge": False,
                     "draft": {"to": ["pat@example.com"], "cc": ["sam@example.com"], "subject": "Lunch on Friday",
                               "body": "Friday at noon works for me.\n\nSam", "replyTo": None}}
    # Portal rewriting its file afterwards changes nothing the owner approves.
    source.write_text(source.read_text().replace("pat@example.com", "someone@example.net"))
    assert only(owner) == shown

    assert approve(owner, shown) == {"proposalId": proposal_id, "outcome": "applied"}
    ((draft),) = owner.google.drafts
    assert "To: pat@example.com\r\n" in draft["raw"] and "Cc: sam@example.com\r\n" in draft["raw"]
    assert "Subject: Lunch on Friday\r\n" in draft["raw"] and "Friday at noon works for me." in draft["raw"]
    assert "someone@example.net" not in draft["raw"] and "Bcc" not in draft["raw"]
    assert owner.google.requests == [("POST", "/drafts")], "a draft is created, never sent"
    assert listing(owner) == {"drafts": [], "more": 0}

    # A repeated approval, say after a lost reply, reports the first outcome and creates nothing.
    assert approve(owner, shown) == {"proposalId": proposal_id, "outcome": "applied"}
    assert len(owner.google.drafts) == 1


def test_a_reply_shows_the_message_it_answers_and_joins_its_thread(owner):
    path = owner.paths.projection / "email.json"
    path.write_text(json.dumps({"schemaVersion": 1, "source": "gmail", "records": [
        {"id": "orig-1", "from": "Pat <pat@example.com>", "subject": "Lunch?"}]}))
    path.chmod(0o640)
    propose(owner, dict(LUNCH, subject="Re: Lunch?", replyToMessageId="orig-1"))
    shown = only(owner)
    assert shown["draft"]["replyTo"] == {"from": "Pat <pat@example.com>", "subject": "Lunch?"}
    assert approve(owner, shown)["outcome"] == "applied"
    ((draft),) = owner.google.drafts
    assert draft["threadId"] == "thread-7" and "In-Reply-To: <orig@example.com>\r\n" in draft["raw"]


def test_drafts_need_their_own_capability_and_can_always_be_declined(world, monkeypatch):
    reader = connected(world, monkeypatch, harness.READ_BOTH)
    assert dict(custody.parse_env(reader.paths.env.read_text()))[custody.DRAFTS_FLAG] == "0"
    proposal_id = propose(reader, LUNCH)
    shown = only(reader)
    with pytest.raises(AccessError, match="gmail-drafts-off"):
        approve(reader, shown)
    assert reader.google.drafts == [] and reader.google.requests == []
    assert c.control(reader.bridge, "connection-decline", {"proposalId": proposal_id}) == {
        "proposalId": proposal_id, "outcome": "declined"}
    assert listing(reader) == {"drafts": [], "more": 0}
    # Undo reverses only Portal's own calendar changes; it never takes a draft.
    with pytest.raises(AccessError, match="invalid-connection-request"):
        c.control(reader.bridge, "connection-undo", {"proposalId": proposal_id})


def test_an_approval_for_anything_but_the_snapshot_creates_nothing(owner):
    propose(owner, LUNCH)
    shown = only(owner)
    with pytest.raises(AccessError, match="gmail-draft-mismatch"):
        approve(owner, shown, sha256="0" * 64)
    with pytest.raises(AccessError, match="gmail-draft-unknown"):
        approve(owner, dict(shown, proposalId="gmail-draft-1761500000099-0badc0de"))
    assert owner.google.drafts == [] and only(owner)["state"] == "waiting"


def test_an_unsafe_draft_is_never_shown_as_approvable(owner):
    propose(owner, dict(LUNCH, subject="Hi\nBcc: someone@example.net"))
    propose(owner, dict(LUNCH, body="[quarantined: email body]"))
    assert [entry["state"] for entry in listing(owner)["drafts"]] == ["invalid", "invalid"]
    assert all(entry["sha256"] is None and entry["draft"] is None for entry in listing(owner)["drafts"])


def test_an_unconfirmed_draft_is_found_by_its_message_id_and_never_created_twice(owner):
    propose(owner, LUNCH)
    shown = only(owner)
    owner.google.lose = "reply"
    assert approve(owner, shown)["outcome"] == "unconfirmed"
    assert only(owner)["state"] == "unconfirmed"
    owner.google.lose = None
    with pytest.raises(AccessError, match="gmail-draft-unconfirmed"):
        approve(owner, shown)
    assert c.control(owner.bridge, "connection-reconcile", {"proposalId": shown["proposalId"]}) == {
        "proposalId": shown["proposalId"], "outcome": "applied"}
    assert len(owner.google.drafts) == 1 and listing(owner) == {"drafts": [], "more": 0}


def test_a_draft_gmail_never_saw_stays_unconfirmed_until_dismissed(owner):
    propose(owner, LUNCH)
    shown = only(owner)
    owner.google.lose = "request"
    assert approve(owner, shown)["outcome"] == "unconfirmed"
    owner.google.lose = None
    # Gmail has no draft with that Message-ID, so nothing settles and nothing is created again.
    assert c.control(owner.bridge, "connection-reconcile", {"proposalId": shown["proposalId"]})["outcome"] == \
        "unconfirmed"
    assert owner.google.drafts == [] and only(owner)["state"] == "unconfirmed"
    c.control(owner.bridge, "connection-decline", {"proposalId": shown["proposalId"]})
    assert listing(owner) == {"drafts": [], "more": 0}
    assert (owner.paths.results / f"{shown['proposalId']}.approved.json").exists(), "the evidence stays"


def test_a_gmail_refusal_settles_the_draft_without_retry(owner):
    propose(owner, LUNCH)
    shown = only(owner)
    owner.google.refuse = True
    assert approve(owner, shown)["outcome"] == "rejected"
    assert listing(owner) == {"drafts": [], "more": 0}
    owner.google.refuse = False
    assert approve(owner, shown)["outcome"] == "rejected"
    assert owner.google.requests == [("POST", "/drafts")]


def test_drafts_count_only_with_the_email_summaries_they_answer(world):
    # Google lets the owner untick any permission; drafts without email summaries stay off.
    assert c._granted(DRAFTS, [harness.CALENDAR, COMPOSE]) == dict(DRAFTS, **{"gmail.read": False,
                                                                            "gmail.drafts": False})
    assert c._granted(DRAFTS, [harness.CALENDAR, harness.GMAIL]) == harness.READ_BOTH
    world.google.token = harness.granted(harness.CALENDAR, COMPOSE)
    flow = harness.begin(world, capabilities=DRAFTS)
    reply = c.control(world.bridge, "connection-complete", {
        "flowId": flow["flowId"], "state": harness.consent_state(world, flow["flowId"]), "code": "4/fixture-code"})
    assert reply["missing"] == ["gmail.read", "gmail.drafts"]
    assert reply["capabilities"]["gmail.drafts"] is False


def test_validation_matches_the_broker_on_the_drafts_portal_writes():
    base = {"schemaVersion": 1, "proposalId": "gmail-draft-1761500000000-0badc0de", "source": "pixel-owner-conversation",
            "action": "create-draft", "status": "pending-operator-approval", "createdAt": "2026-10-26T15:00:00.000Z",
            "boundary": "This file is a proposal only. No Gmail draft has been created."}
    pat, sam = {"email": "pat@example.com"}, {"email": "sam@example.com"}
    cases = [
        LUNCH, dict(LUNCH, subject=""), {"to": [pat], "body": "Hi"}, dict(LUNCH, replyToMessageId="18c2f0a9b7e4d123"),
        dict(LUNCH, cc=[]), dict(LUNCH, body="Tabs\tand\r\nbreaks"), dict(LUNCH, to=[pat] * 1, cc=[sam] * 1),
        dict(LUNCH, to=[]), dict(LUNCH, to=[pat, {"email": "PAT@example.com"}]), dict(LUNCH, cc=[pat]),
        dict(LUNCH, to=[{"email": "not-an-address"}]), dict(LUNCH, to=[{"email": "pat@example.com", "name": "Pat"}]),
        dict(LUNCH, to=[{"email": f"p{index}@example.com"} for index in range(50)]),
        dict(LUNCH, subject="Hi\nBcc: x@example.com"), dict(LUNCH, subject="x" * 999), dict(LUNCH, body=" "),
        dict(LUNCH, body="Ring\x07"), dict(LUNCH, body="n" * 100001), dict(LUNCH, replyToMessageId="../etc"),
        dict(LUNCH, replyToMessageId=7), dict(LUNCH, bcc=[sam]), dict(LUNCH, body="[quarantined: email body]"),
        dict(LUNCH, to=[{"email": "[withheld]"}]), {"body": "No one to send it to"},
    ]
    for values in cases:
        value = dict(base, values=values)
        try:
            BROKER.validate_draft_proposal(json.loads(json.dumps(value)), base["proposalId"])
            expected = True
        except (RuntimeError, TypeError):
            expected = False
        try:
            drafts.validate(json.loads(json.dumps(value)), base["proposalId"])
            ours = True
        except ValueError:
            ours = False
        assert ours == expected, values
    for broken in (dict(base, action="send"), dict(base, status="pending-bounded-direct"), dict(base, source="email"),
                   dict(base, boundary="This file is a proposal only. No Calendar mutation has occurred.")):
        with pytest.raises(ValueError):
            drafts.validate(dict(broken, values=LUNCH), base["proposalId"])


def test_status_counts_waiting_drafts_apart_from_calendar_changes_and_never_their_content(owner):
    propose(owner, LUNCH)
    propose(owner, dict(LUNCH, subject="Second"))
    calendar_harness.propose(owner, "create", calendar_harness.DENTIST)
    assert drafts.pending_count(owner.paths) == 2
    report = status.report(surface="linux-systemd", record_path=owner.record, token_path=owner.paths.token,
                           status_path=owner.paths.projection / "status.json",
                           lifecycle_path=owner.paths.state / "missing.json", owners=(0, os.getuid()),
                           broker_state=owner.paths.state, now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    google = status.validate_public(report)["connectors"]["google"]
    assert (google["waitingDrafts"], google["waitingChanges"]) == (2, 1)
    assert "Lunch" not in json.dumps(report) and "pat@example.com" not in json.dumps(report)
