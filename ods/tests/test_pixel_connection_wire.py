"""The Edge relay and the host agent accept exactly the same connected-apps status and actions."""
import ast
import copy
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_connections import status  # noqa: E402

SPEC = importlib.util.spec_from_file_location("edge_access_mode", ROOT / "extensions/services/pixel-edge/access_mode.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

OWNERS = (0, os.getuid()) if hasattr(os, "getuid") else (0,)
PROPOSAL = "calendar-1761500000000-0badc0de"
DRAFT_ID = "gmail-draft-1761500000000-0badc0de"
NOW = datetime(2026, 10, 25, 20, 30, tzinfo=timezone.utc)
RECORD = {
    "schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
    "ownerTimeZone": "America/Chicago",
    "connectors": {"google": {
        "status": "connected", "account": "owner@example.com", "calendarId": "primary", "gmailWindowDays": 180,
        "capabilities": {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
                         "gmail.read": True, "gmail.drafts": False},
        "cloudModels": False, "connectedAt": "2026-10-25T20:00:00Z"}},
}


def broker(error_class=None):
    source = {"enabled": True, "state": "error" if error_class else "ok", "errorClass": error_class,
              "httpStatus": 503 if error_class else None, "errorType": None, "lastAttemptAt": "2026-10-25T20:29:00Z",
              "lastSuccessAt": None, "failingSince": "2026-10-25T20:25:00Z" if error_class else None,
              "recordCount": 0, "complete": None if error_class else True}
    return {"schemaVersion": 1, "kind": "pixel-source-broker-status", "generatedAt": "2026-10-25T20:29:30Z",
            "grantedScopes": [], "sources": {"email": source, "calendar": source}}


def journal(phase, error=None):
    consent = phase == "consent"
    return {"kind": "connection", "connector": "google", "phase": phase,
            "flowId": "a" * 32 if consent else None, "stateHash": "b" * 64 if consent else None,
            "redirectUri": "http://127.0.0.1:53682" if consent else None,
            "expiresAt": "2026-10-25T20:45:00Z" if consent else None,
            "request": {"capabilities": RECORD["connectors"]["google"]["capabilities"], "cloudModels": False,
                        "ownerTimeZone": None} if consent else None,
            "error": error, "updatedAt": "2026-10-25T20:30:00Z"}


def samples():
    values = [status.unavailable("darwin"), status.unavailable("windows")]
    for record, token, broker_value, pending in (
            (None, False, None, None), (RECORD, False, None, None), (RECORD, True, None, None),
            (RECORD, True, broker(), None), (RECORD, True, broker("network"), None),
            (RECORD, True, broker("invalid-grant"), None), (None, False, None, journal("consent")),
            (RECORD, True, None, journal("activating", "runtime-busy")), (None, False, None, journal("deactivating")),
            (RECORD, False, None, journal("consent")), (None, False, None, journal("failed", "access-denied")),
            (RECORD, True, broker(), "waiting-change"), (RECORD, True, broker(), "waiting-draft")):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o755)
            if record:
                (root / "record.json").write_text(json.dumps(record))
                os.chmod(root / "record.json", 0o640)
            if token:
                (root / "token.json").write_text("{}")
            if broker_value:
                (root / "status.json").write_text(json.dumps(broker_value))
            if pending in ("waiting-change", "waiting-draft"):
                (root / "proposals").mkdir()
                name = PROPOSAL if pending == "waiting-change" else DRAFT_ID
                (root / "proposals" / f"{name}.json").write_text(json.dumps({"status": "pending-operator-approval"}))
            elif pending:
                (root / "connection.json").write_text(json.dumps(pending))
                os.chmod(root / "connection.json", 0o600)
            values.append(status.report(surface="wsl-systemd", record_path=root / "record.json",
                                        token_path=root / "token.json", status_path=root / "status.json",
                                        lifecycle_path=root / "connection.json", now=NOW, owners=OWNERS,
                                        broker_state=root))
    values.extend(dict(copy.deepcopy(values[-1]), googleAuth={'mode': 'managed', 'state': state,
                  'advancedAvailable': True}) for state in ('configured', 'not-configured', 'unavailable'))
    return values


MUTATIONS = (
    lambda v: v['connectors']['google'].update(authMode=None),
    lambda v: v['connectors']['google'].update(authMode='owner-client'),
    lambda v: v['connectors']['google'].update(authMode=True),
    lambda v: v.update(googleAuth=None),
    lambda v: v.update(googleAuth={'mode': 'managed', 'state': 'configured', 'advancedAvailable': 1}),
    lambda v: v.update(googleAuth={'mode': 'managed', 'state': 'verified', 'advancedAvailable': True}),
    lambda v: v.update(googleAuth={'mode': 'managed', 'state': 'configured', 'advancedAvailable': True,
                                 'clientSecret': 'private-sentinel'}),
    lambda v: v.update(extra=1),
    lambda v: v.update(schemaVersion=2),
    lambda v: v.update(available="yes"),
    lambda v: v.update(reason="Has Spaces"),
    lambda v: v["connectors"].update(other=None),
    lambda v: v["connectors"]["google"].update(state="unknown"),
    lambda v: v["connectors"]["google"].update(account="not-an-email"),
    lambda v: v["connectors"]["google"].update(cloudModels="no"),
    lambda v: v["connectors"]["google"]["sources"].update(social=None),
    lambda v: v["connectors"]["google"].update(grantedScopes=["https://example.com/scope"]),
    lambda v: v["connectors"]["google"].pop("pending"),
    lambda v: v["connectors"]["google"].pop("waitingChanges"),
    lambda v: v["connectors"]["google"].update(waitingChanges=-1),
    lambda v: v["connectors"]["google"].update(waitingChanges=True),
    lambda v: v["connectors"]["google"].update(waitingChanges=51),
    lambda v: v["connectors"]["google"].update(waitingChanges=None if v["connectors"]["google"]["waitingChanges"] is not None
                                               else 0),
    lambda v: v["connectors"]["google"].pop("waitingDrafts"),
    lambda v: v.pop("plugins"),
    lambda v: v.update(plugins="on"),
    lambda v: v.update(plugins={"memory-wiki": "yes"}),
    lambda v: v.update(plugins={"Not An Id": True}),
    lambda v: v["connectors"]["google"].update(waitingDrafts=-1),
    lambda v: v["connectors"]["google"].update(waitingDrafts=True),
    lambda v: v["connectors"]["google"].update(waitingDrafts=51),
    lambda v: v["connectors"]["google"].update(waitingDrafts=None if v["connectors"]["google"]["waitingDrafts"] is not None
                                               else 0),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "consent", "expiresAt": None,
                                                        "error": None, "since": "2026-10-25T20:30:00Z"}),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "deactivating", "expiresAt": None,
                                                        "error": None, "since": "2026-10-25T20:30:00Z"}),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "activating", "expiresAt": None,
                                                        "error": "Has Spaces", "since": "2026-10-25T20:30:00Z"}),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "activating", "expiresAt": None,
                                                        "error": None, "since": None}),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "failed", "expiresAt": None,
                                                        "error": None, "since": "2026-10-25T20:30:00Z"}),
    lambda v: v["connectors"]["google"].update(pending={"action": "disconnect", "phase": "failed", "expiresAt": None,
                                                        "error": "access-denied", "since": "2026-10-25T20:30:00Z"}),
    lambda v: v["connectors"]["google"].update(pending={"action": "connect", "phase": "consent", "error": None,
                                                        "expiresAt": "2026-10-25T20:45:00Z", "flowId": "a" * 32,
                                                        "since": "2026-10-25T20:30:00Z"}),
)


FLOW = "a" * 32
CAPABILITIES = RECORD["connectors"]["google"]["capabilities"]
BEGIN = {"connector": "google", "capabilities": CAPABILITIES, "cloudModels": False, "ownerTimeZone": "America/Chicago",
         "loginHint": None, "redirectPort": 53682,
         "client": {"clientId": "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com",
                    "clientSecret": "test-secret-test", "projectId": "ods-owner-calendar"}}
REQUESTS = (
    ("connection-begin", BEGIN),
    ('connection-begin', {key: value for key, value in dict(BEGIN, authMode='managed').items() if key != 'client'}),
    ('connection-begin', dict(BEGIN, authMode='advanced')),
    ("connection-begin", dict(BEGIN, ownerTimeZone=None, loginHint="owner@example.com", cloudModels=True)),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "code": "4/0AfJohX-fixture"}),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "error": "access-denied"}),
    ("connection-activate", {}), ("connection-cancel", {"flowId": FLOW}),
    ("connection-disconnect", {"connector": "google"}), ("connection-sync", {}),
    ("connection-proposals", {}), ("connection-approve", {"proposalId": PROPOSAL, "sha256": "c" * 64}),
    ("connection-decline", {"proposalId": PROPOSAL}), ("connection-reconcile", {"proposalId": PROPOSAL}),
    ("connection-activity", {}), ("connection-undo", {"proposalId": PROPOSAL}),
    ("connection-drafts", {}), ("connection-approve", {"proposalId": DRAFT_ID, "sha256": "c" * 64}),
    ("connection-decline", {"proposalId": DRAFT_ID}), ("connection-reconcile", {"proposalId": DRAFT_ID}),
    ("connection-plugin", {"plugin": "memory-wiki", "enabled": True}),
    ("connection-plugin", {"plugin": "memory-wiki", "enabled": False}),
)
BAD_REQUESTS = (
    ('connection-begin', dict(BEGIN, authMode='managed')),
    ('connection-begin', dict(BEGIN, authMode='managed', client=None)),
    ('connection-begin', dict(BEGIN, authMode=None)),
    ('connection-begin', {key: value for key, value in dict(BEGIN, authMode='advanced').items() if key != 'client'}),
    ("connection-begin", dict(BEGIN, redirectPort=80)), ("connection-begin", dict(BEGIN, extra=1)),
    ("connection-begin", dict(BEGIN, client=dict(BEGIN["client"], clientId="not-a-google-client"))),
    ("connection-begin", dict(BEGIN, capabilities=dict(CAPABILITIES, send=True))),
    ("connection-begin", dict(BEGIN, cloudModels="no")), ("connection-begin", dict(BEGIN, connector="dropbox")),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43}),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "code": "4/x", "error": "access-denied"}),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "error": "server_error"}),
    ("connection-complete", {"flowId": FLOW, "state": "short", "code": "4/x"}),
    ("connection-activate", {"force": True}), ("connection-cancel", {"flowId": "A" * 32}),
    ("connection-disconnect", {}), ("connection-status", {}), ("connection-sync", []),
    ("connection-proposals", {"all": True}), ("connection-approve", {"proposalId": PROPOSAL}),
    ("connection-approve", {"proposalId": PROPOSAL, "sha256": "C" * 64}),
    ("connection-approve", {"proposalId": PROPOSAL, "sha256": "c" * 64, "force": True}),
    ("connection-decline", {"proposalId": "calendar-1-0badc0de"}),
    ("connection-reconcile", {"proposalId": PROPOSAL, "sha256": "c" * 64}),
    ("connection-activity", {"days": 30}), ("connection-undo", {"proposalId": PROPOSAL, "sha256": "c" * 64}),
    ("connection-undo", {}),
    # Undo reverses only Portal's own calendar changes; a draft is never undone, and nothing is sent.
    ("connection-undo", {"proposalId": DRAFT_ID}), ("connection-drafts", {"all": True}),
    ("connection-approve", {"proposalId": "gmail-draft-1-0badc0de", "sha256": "c" * 64}),
    ("connection-decline", {"proposalId": "gmail-send-1761500000000-0badc0de"}),
    ("connection-plugin", {"plugin": "memory-wiki"}), ("connection-plugin", {"plugin": "memory-wiki", "enabled": 1}),
    ("connection-plugin", {"plugin": "Memory Wiki", "enabled": True}),
    ("connection-plugin", {"plugin": "memory-wiki", "enabled": True, "config": {"vaultMode": "unsafe-local"}}),
)
CHANGE = {"summary": "Dentist", "start": "2026-10-27T15:00:00-05:00", "end": "2026-10-27T16:00:00-05:00",
          "allDay": False, "timeZone": "America/Chicago", "location": "12 Main St", "description": "Bring\nthe form",
          "attendees": ["pat@example.com"], "sendUpdates": "all"}
WAITING = {"proposalId": PROPOSAL, "sha256": "c" * 64, "state": "waiting", "action": "create",
           "createdAt": "2026-10-26T15:00:00.000Z", "change": CHANGE, "event": None, "tooLarge": False}
UPDATE = dict(WAITING, state="unconfirmed", action="update",
              change=dict.fromkeys(CHANGE, None) | {"start": CHANGE["start"], "end": CHANGE["end"], "sendUpdates": "none"},
              event={"title": "Team sync", "start": "2026-10-27T10:00:00-05:00", "end": None, "allDay": False})
UNREADABLE = {"proposalId": PROPOSAL, "sha256": None, "state": "invalid", "action": None, "createdAt": None,
              "change": None, "event": None, "tooLarge": False}
TOO_LARGE = dict(WAITING, sha256=None, change=None, tooLarge=True)
# Private changes Portal made by itself, as root lists them with Undo.
ADDED = {"proposalId": PROPOSAL, "action": "create", "title": "Focus time", "start": CHANGE["start"],
         "end": CHANGE["end"], "allDay": False, "previous": None, "appliedAt": "2026-10-26T15:00:01.123456Z",
         "undo": "available"}
MOVED = dict(ADDED, action="update", title=None, undo="syncing",
             previous={"start": "2026-10-27", "end": "2026-10-28", "allDay": True})
# Gmail drafts Portal wrote, as root lists them for approval.
GMAIL_DRAFT = {"to": ["pat@example.com"], "cc": ["sam@example.com"], "subject": "Thursday",
               "body": "Hi Pat,\n\nThursday at 3 works.\n\tSam", "replyTo": None}
DRAFT = {"proposalId": DRAFT_ID, "sha256": "d" * 64, "state": "waiting", "createdAt": "2026-10-26T15:00:00.000Z",
         "draft": GMAIL_DRAFT, "tooLarge": False}
REPLY = dict(DRAFT, state="unconfirmed", draft=dict(GMAIL_DRAFT, cc=[], subject="Re: Lunch",
                                                    replyTo={"from": "Pat <pat@example.com>", "subject": "Lunch"}))
UNREAD_DRAFT = {"proposalId": DRAFT_ID, "sha256": None, "state": "invalid", "createdAt": None, "draft": None,
                "tooLarge": False}
LONG_DRAFT = dict(DRAFT, sha256=None, draft=None, tooLarge=True)
REPLIES = (
    ("connection-begin", {"flowId": FLOW, "authUrl": "https://accounts.google.com/o/oauth2/auth?client_id=x&state=y",
                          "redirectUri": "http://127.0.0.1:53682", "expiresAt": "2026-10-25T20:45:00Z"}),
    ("connection-complete", {"outcome": "connected", "errorClass": None, "account": "owner@example.com",
                             "capabilities": CAPABILITIES, "missing": ["gmail.read"]}),
    ("connection-complete", {"outcome": "failed", "errorClass": "account-unverified", "account": None,
                             "capabilities": None, "missing": []}),
    ("connection-activate", {"outcome": "applied"}), ("connection-activate", {"outcome": "unchanged"}),
    ("connection-cancel", {"outcome": "cancelled"}), ("connection-sync", {"outcome": "started"}),
    ("connection-disconnect", {"outcome": "disconnected", "revoked": False}),
    ("connection-proposals", {"proposals": [], "more": 0}),
    ("connection-proposals", {"proposals": [WAITING, UPDATE, UNREADABLE, TOO_LARGE], "more": 3}),
    ("connection-approve", {"proposalId": PROPOSAL, "outcome": "applied"}),
    ("connection-approve", {"proposalId": PROPOSAL, "outcome": "unconfirmed"}),
    ("connection-reconcile", {"proposalId": PROPOSAL, "outcome": "waiting"}),
    ("connection-decline", {"proposalId": PROPOSAL, "outcome": "declined"}),
    ("connection-activity", {"changes": [], "more": 0}),
    ("connection-activity", {"changes": [ADDED, MOVED], "more": 4}),
    ("connection-undo", {"proposalId": PROPOSAL, "outcome": "applied"}),
    ("connection-undo", {"proposalId": PROPOSAL, "outcome": "rejected"}),
    ("connection-drafts", {"drafts": [], "more": 0}),
    ("connection-drafts", {"drafts": [DRAFT, REPLY, UNREAD_DRAFT, LONG_DRAFT], "more": 2}),
    ("connection-approve", {"proposalId": DRAFT_ID, "outcome": "applied"}),
    ("connection-reconcile", {"proposalId": DRAFT_ID, "outcome": "unconfirmed"}),
    ("connection-decline", {"proposalId": DRAFT_ID, "outcome": "declined"}),
    ("connection-plugin", {"outcome": "applied"}), ("connection-plugin", {"outcome": "unchanged"}),
)
BAD_REPLIES = (
    ("connection-begin", {"flowId": FLOW, "authUrl": "https://example.com/o/oauth2/auth?x",
                          "redirectUri": "http://127.0.0.1:53682", "expiresAt": "2026-10-25T20:45:00Z"}),
    ("connection-begin", {"flowId": FLOW, "authUrl": "https://accounts.google.com/o/oauth2/auth?x",
                          "redirectUri": "http://192.168.1.2:53682", "expiresAt": "2026-10-25T20:45:00Z"}),
    ("connection-complete", {"outcome": "connected", "errorClass": None, "account": None,
                             "capabilities": CAPABILITIES, "missing": []}),
    ("connection-complete", {"outcome": "failed", "errorClass": None, "account": None, "capabilities": None,
                             "missing": []}),
    ("connection-complete", {"outcome": "connected", "errorClass": None, "account": "owner@example.com",
                             "capabilities": CAPABILITIES, "missing": ["send"]}),
    ("connection-activate", {"outcome": "cancelled"}), ("connection-sync", {"outcome": "started", "token": "x"}),
    ("connection-disconnect", {"outcome": "disconnected"}), ("connection-cancel", []),
    ("connection-approve", {"proposalId": PROPOSAL, "outcome": "waiting"}),
    ("connection-decline", {"proposalId": PROPOSAL, "outcome": "applied"}),
    ("connection-approve", {"proposalId": "calendar-x", "outcome": "applied"}),
    ("connection-proposals", {"proposals": [], "more": -1}),
    ("connection-proposals", {"proposals": [WAITING] * 21, "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, sha256=None)], "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, extra=1)], "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, change=dict(CHANGE, location="Room\x07"))], "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, change=dict(CHANGE, attendees=["not-an-address"]))], "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, change=dict(CHANGE, description="n" * 20001))], "more": 0}),
    ("connection-proposals", {"proposals": [dict(WAITING, change=dict(CHANGE, sendUpdates="some"))], "more": 0}),
    ("connection-proposals", {"proposals": [dict(UNREADABLE, action="create")], "more": 0}),
    ("connection-proposals", {"proposals": [dict(TOO_LARGE, change=CHANGE)], "more": 0}),
    ("connection-proposals", {"proposals": [dict(UPDATE, event=dict(UPDATE["event"], allDay=None))], "more": 0}),
    ("connection-undo", {"proposalId": PROPOSAL, "outcome": "declined"}),
    ("connection-activity", {"changes": [dict(ADDED, action="delete")], "more": 0}),
    ("connection-activity", {"changes": [dict(ADDED, undo="maybe")], "more": 0}),
    ("connection-activity", {"changes": [dict(ADDED, title="Ring\x07")], "more": 0}),
    ("connection-activity", {"changes": [dict(MOVED, previous={"start": None})], "more": 0}),
    ("connection-activity", {"changes": [dict(ADDED, extra=1)], "more": 0}),
    ("connection-activity", {"changes": [ADDED] * 21, "more": 0}),
    ("connection-undo", {"proposalId": DRAFT_ID, "outcome": "applied"}),
    ("connection-proposals", {"proposals": [DRAFT], "more": 0}),
    ("connection-drafts", {"drafts": [WAITING], "more": 0}),
    ("connection-drafts", {"drafts": [DRAFT] * 21, "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, extra=1)], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, sha256=None)], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, subject="Hi\nBcc: x@example.com"))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, to=[]))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, to=["not-an-address"]))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, cc=["a@example.com"] * 50))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, body=" "))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, body="n" * 100001))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, body="Ring\x07"))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, bcc=["x@example.com"]))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(DRAFT, draft=dict(GMAIL_DRAFT, replyTo={"from": None}))], "more": 0}),
    ("connection-drafts", {"drafts": [dict(UNREAD_DRAFT, draft=GMAIL_DRAFT)], "more": 0}),
    ("connection-drafts", {"drafts": [dict(LONG_DRAFT, draft=GMAIL_DRAFT)], "more": 0}),
    ("connection-drafts", {"drafts": [], "more": -1}),
    ("connection-plugin", {"outcome": "started"}), ("connection-plugin", {"outcome": "applied", "plugin": "x"}),
)
VALIDATORS = ("valid_connection_action", "public_connection_action", "_text", "_attendee", "_capability_flags", "_words",
              "_calendar_change", "_calendar_event", "_calendar_entry", "_calendar_moment", "_calendar_activity",
              "_suggestion", "_gmail_draft", "_draft_entry")
CONSTANTS = ("_CONNECTION_OPERATIONS", "_CAPABILITY_IDS", "_FLOW", "_STATE", "_CODE", "_CLIENT_ID", "_CLIENT_SECRET",
             "_PROJECT", "_ACCOUNT", "_TIME_ZONE", "_SECOND", "_REASON", "_AUTH_URL", "_PROPOSAL", "_DRAFT", "_HEX",
             "_ACTION_TEXT_LIMIT", "_CONTROL", "_CHANGE_OUTCOMES", "_DRAFT_OPERATIONS", "_PLUGIN")


class ActionWireTests(unittest.TestCase):
    """The host relay and the Edge pass the same custodian actions, so neither can widen what root sees."""

    def test_the_host_relay_and_the_edge_run_identical_validators(self):
        import pixel_access_relay as relay
        for name in VALIDATORS:
            self.assertEqual(inspect.getsource(getattr(relay, name)), inspect.getsource(getattr(edge, name)), name)
        for name in CONSTANTS:
            ours, theirs = getattr(relay, name), getattr(edge, name)
            self.assertEqual(getattr(ours, "pattern", ours), getattr(theirs, "pattern", theirs), name)

    def test_both_accept_every_request_the_host_sends_and_refuse_the_rest(self):
        import pixel_access_relay as relay
        for operation, request in REQUESTS:
            payload = {"operation": operation, "request": copy.deepcopy(request)}
            self.assertTrue(relay.valid_connection_action(payload), operation)
            self.assertTrue(edge.valid_connection_action(payload), operation)
        for operation, request in BAD_REQUESTS:
            payload = {"operation": operation, "request": copy.deepcopy(request)}
            self.assertFalse(relay.valid_connection_action(payload), (operation, request))
            self.assertFalse(edge.valid_connection_action(payload), (operation, request))

    @unittest.skipIf(os.name == "nt", "the root coordinator runs on Linux")
    def test_root_accepts_whatever_both_relays_pass(self):
        from pixel_connections import coordinator
        for operation, request in REQUESTS:
            if operation == "connection-begin":
                coordinator._begin_request(copy.deepcopy(request))
            elif operation == "connection-complete":
                coordinator._complete_request(copy.deepcopy(request))
            elif operation == "connection-plugin":
                coordinator._plugin_request(copy.deepcopy(request))
            elif operation in ("connection-approve", "connection-decline", "connection-reconcile", "connection-undo"):
                coordinator._change_request(copy.deepcopy(request), set(request),
                                            drafts_too=operation != "connection-undo")

    @unittest.skipUnless(hasattr(os, "fchown"), "root keeps calendar snapshots on Linux")
    def test_both_relays_pass_every_change_list_root_builds(self):
        import pixel_access_relay as relay
        from pixel_connections import approvals
        with tempfile.TemporaryDirectory() as directory:
            dirs = approvals.Dirs.under(Path(directory))
            for item in dirs:
                item.mkdir()
            proposals = [("create", dict(CHANGE, attendees=[{"email": "pat@example.com"}]) | {"allDay": False}),
                         ("update", {"eventId": "evt-1", "expectedEtag": '"e1"', "attendees": []}),
                         ("delete", {"eventId": "evt-1", "expectedEtag": '"e1"'}),
                         ("create", dict(CHANGE, attendees=[], description="\u00e9" * 20000)), ("create", "broken")]
            for index, (action, values) in enumerate(proposals):
                proposal_id = f"calendar-17615000000{index:02d}-0badc0de"
                values = {key: value for key, value in values.items() if value is not None} if type(values) is dict else values
                (dirs.proposals / f"{proposal_id}.json").write_text(json.dumps({
                    "schemaVersion": 1, "proposalId": proposal_id, "source": "pixel-owner-conversation",
                    "action": action, "status": "pending-operator-approval", "createdAt": "2026-10-26T15:00:00.000Z",
                    "values": values, "boundary": "This file is a proposal only. No Calendar mutation has occurred."}))
                os.chmod(dirs.proposals / f"{proposal_id}.json", 0o640)
            (dirs.projection / "calendar.json").write_text(json.dumps({"records": [
                {"id": "evt-1", "title": "Team sync", "start": {"date": "2026-10-27"}, "end": {"date": "2026-10-28"}}]}))
            os.chmod(dirs.projection / "calendar.json", 0o640)
            listed = approvals.listing(dirs, approvals.Ids(os.getuid(), os.getgid(), os.getuid()))
        self.assertEqual([entry["state"] for entry in listed["proposals"]], ["waiting"] * 4 + ["invalid"])
        self.assertTrue(listed["proposals"][3]["tooLarge"])
        self.assertEqual(listed["proposals"][1]["event"], {"title": "Team sync", "start": "2026-10-27",
                                                           "end": "2026-10-28", "allDay": True})
        self.assertEqual(relay.public_connection_action("connection-proposals", copy.deepcopy(listed)), listed)
        self.assertEqual(edge.public_connection_action("connection-proposals", copy.deepcopy(listed)), listed)

    @unittest.skipUnless(hasattr(os, "fchown"), "root keeps draft snapshots on Linux")
    def test_both_relays_pass_every_draft_list_root_builds(self):
        import pixel_access_relay as relay
        from pixel_connections import approvals, drafts
        with tempfile.TemporaryDirectory() as directory:
            dirs = approvals.Dirs.under(Path(directory))
            for item in dirs:
                item.mkdir()
            proposals = [{"to": [{"email": "pat@example.com"}], "cc": [{"email": "sam@example.com"}], "subject": "Plan",
                          "body": "Hi Pat,\n\tThursday?"},
                         {"to": [{"email": "pat@example.com"}], "body": "Sounds good.", "replyToMessageId": "msg-1"},
                         {"to": [{"email": "pat@example.com"}], "body": "Unknown thread.", "replyToMessageId": "msg-9"},
                         {"to": [{"email": "pat@example.com"}], "subject": "Long", "body": "é" * 30000},
                         {"to": [{"email": "pat@example.com"}], "subject": "Two\nlines", "body": "Header injection"}]
            for index, values in enumerate(proposals):
                proposal_id = f"gmail-draft-17615000000{index:02d}-0badc0de"
                (dirs.proposals / f"{proposal_id}.json").write_text(json.dumps({
                    "schemaVersion": 1, "proposalId": proposal_id, "source": "pixel-owner-conversation",
                    "action": "create-draft", "status": "pending-operator-approval",
                    "createdAt": "2026-10-26T15:00:00.000Z", "values": values,
                    "boundary": "This file is a proposal only. No Gmail draft has been created."}))
                os.chmod(dirs.proposals / f"{proposal_id}.json", 0o640)
            (dirs.projection / "email.json").write_text(json.dumps({"records": [
                {"id": "msg-1", "from": "Pat <pat@example.com>", "subject": "Lunch?"}]}))
            os.chmod(dirs.projection / "email.json", 0o640)
            listed = drafts.listing(dirs, approvals.Ids(os.getuid(), os.getgid(), os.getuid()))
        self.assertEqual([entry["state"] for entry in listed["drafts"]], ["waiting"] * 4 + ["invalid"])
        self.assertEqual(listed["drafts"][0]["draft"], {"to": ["pat@example.com"], "cc": ["sam@example.com"],
                                                        "subject": "Plan", "body": "Hi Pat,\n\tThursday?",
                                                        "replyTo": None})
        self.assertEqual(listed["drafts"][1]["draft"]["replyTo"], {"from": "Pat <pat@example.com>", "subject": "Lunch?"})
        self.assertEqual(listed["drafts"][2]["draft"]["replyTo"], {"from": None, "subject": None})
        self.assertTrue(listed["drafts"][3]["tooLarge"])
        self.assertEqual(relay.public_connection_action("connection-drafts", copy.deepcopy(listed)), listed)
        self.assertEqual(edge.public_connection_action("connection-drafts", copy.deepcopy(listed)), listed)

    def test_both_pass_every_reply_the_custodian_gives_and_withhold_the_rest(self):
        import pixel_access_relay as relay
        for operation, reply in REPLIES:
            self.assertEqual(relay.public_connection_action(operation, copy.deepcopy(reply)), reply)
            self.assertEqual(edge.public_connection_action(operation, copy.deepcopy(reply)), reply)
        for operation, reply in BAD_REPLIES:
            with self.assertRaises(ValueError):
                relay.public_connection_action(operation, copy.deepcopy(reply))
            with self.assertRaises(ValueError):
                edge.public_connection_action(operation, copy.deepcopy(reply))


class WireContractTests(unittest.TestCase):
    def test_reviewed_plugin_change_crosses_root_host_and_edge_without_private_metadata(self):
        import pixel_access_client
        import pixel_access_relay as relay
        from pixel_connections import plugins

        original = status.unavailable('wsl-systemd')
        changes = [dict(plugin=plugin, enabled=enabled, error=error)
                   for plugin in plugins.IDS for enabled in (True, False)
                   for error in (None, 'connection-runtime-proof-failed')]
        changes.append(dict(plugin=None, enabled=None, error='connection-transition-unavailable'))
        for change in changes:
            value = dict(copy.deepcopy(original), pluginChange=change)
            with self.subTest(change=change):
                self.assertEqual(status.validate_public(copy.deepcopy(value)), value)
                self.assertEqual(edge.public_connections(copy.deepcopy(value)), value)
                with patch.object(relay.platform, 'system', return_value='Linux'), \
                        patch.object(pixel_access_client, 'request_access', return_value=(200, value)):
                    self.assertEqual(relay.request_runtime_connections(config={}), (200, value))
        self.assertNotIn('pluginChange', edge.public_connections(original))

    def test_malformed_plugin_change_is_withheld_by_root_and_edge(self):
        valid = dict(plugin='memory-wiki', enabled=True, error=None)
        changes = [None, [], 'pending', {},
                   {key: value for key, value in valid.items() if key != 'error'},
                   dict(valid, token='private-sentinel'),
                   dict(valid, beforeRecordSha='a' * 64),
                   dict(valid, plugin='unknown-plugin'), dict(valid, plugin=True),
                   dict(valid, plugin=None), dict(valid, enabled=None),
                   dict(valid, enabled=0), dict(valid, enabled=1), dict(valid, enabled='true'),
                   dict(valid, error='private path or credential'), dict(valid, error='a' * 65),
                   dict(valid, error='connection-failed\n'), dict(valid, error=True),
                   dict(plugin=None, enabled=None, error=None),
                   dict(plugin=None, enabled=None, error=''),
                   dict(plugin=None, enabled=None, error='a' * 65)]
        for change in changes:
            value = dict(status.unavailable('wsl-systemd'), pluginChange=change)
            with self.subTest(change=change):
                with self.assertRaises(status.StatusError):
                    status.validate_public(copy.deepcopy(value))
                with self.assertRaisesRegex(ValueError, '^invalid-connection-status$'):
                    edge.public_connections(copy.deepcopy(value))

    def test_legacy_account_mode_absence_is_preserved_and_managed_mode_is_independent_of_catalog(self):
        value = next(item for item in samples() if item['connectors']['google']['state'] == 'connected')
        legacy = copy.deepcopy(value)
        legacy['connectors']['google'].pop('authMode', None)
        self.assertEqual(status.validate_public(legacy), legacy)
        self.assertEqual(edge.public_connections(legacy), legacy)
        self.assertNotIn('authMode', legacy['connectors']['google'])
        managed = copy.deepcopy(value)
        managed['connectors']['google']['authMode'] = 'managed'
        self.assertEqual(status.validate_public(managed), managed)
        self.assertEqual(edge.public_connections(managed), managed)

    def test_actual_root_status_branch_resolves_only_the_safe_deployment_projection(self):
        from pixel_connections import google_app
        tree = ast.parse((ROOT / 'extensions/services/pixel-agent/host/access_mode_server.py').read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and any(isinstance(item, ast.ImportFrom) and item.module == 'pixel_connections.google_app'
                              for item in node.body))
        auth = {'mode': 'managed', 'state': 'not-configured', 'advancedAvailable': True}
        original = status.unavailable('windows')
        context = {'platform': SimpleNamespace(release=lambda: 'fixture-microsoft')}
        with patch.object(status, 'report', return_value=original) as report, \
                patch.object(google_app, 'public_status', return_value=auth) as projection:
            exec(compile(ast.Module(body=branch.body, type_ignores=[]), '<actual Root connection status>', 'exec'), context)
        self.assertEqual(context['body'], dict(original, googleAuth=auth))
        self.assertNotIn('googleAuth', original)
        report.assert_called_once_with(surface='wsl-systemd')
        projection.assert_called_once_with()

    def test_host_relays_only_root_metadata_on_linux_or_windows_without_reading_client_files(self):
        import pixel_access_client
        import pixel_access_relay as relay
        from pixel_connections import google_app
        value = dict(status.unavailable('windows'), googleAuth={
            'mode': 'managed', 'state': 'configured', 'advancedAvailable': True})
        with patch.object(google_app, 'load', side_effect=AssertionError('Host must not read deployment client')):
            with patch.object(relay.platform, 'system', return_value='Linux'), \
                    patch.object(pixel_access_client, 'request_access', return_value=(200, value)):
                self.assertEqual(relay.request_runtime_connections(config={}), (200, value))
            with patch.object(relay.platform, 'system', return_value='Windows'), \
                    patch.object(relay, '_request_runtime_controller', return_value=(200, value)) as transport:
                self.assertEqual(relay.request_runtime_connections(config={'PIXEL_OPENWEBUI_KEY': 'fixture'}), (200, value))
                transport.assert_called_once()
            broken = dict(value, googleAuth=dict(value['googleAuth'], clientSecret='private-sentinel'))
            with patch.object(relay.platform, 'system', return_value='Linux'), \
                    patch.object(pixel_access_client, 'request_access', return_value=(200, broken)):
                with self.assertRaisesRegex(relay.AccessRelayError, '^connections-unavailable$'):
                    relay.request_runtime_connections(config={})

    def test_both_relays_accept_every_status_the_controller_produces(self):
        produced = samples()
        # Every step in progress reaches Settings, and none of the flow's secrets do.
        phases = [value["connectors"]["google"]["pending"] for value in produced]
        self.assertEqual({item["phase"] for item in phases if item}, {"consent", "activating", "deactivating", "failed"})
        self.assertNotIn("b" * 64, json.dumps(produced))
        self.assertNotIn("a" * 32, json.dumps(produced))
        for value in produced:
            with self.subTest(state=value["connectors"]["google"]["state"], surface=value["surface"]):
                self.assertEqual(status.validate_public(copy.deepcopy(value)), value)
                self.assertEqual(edge.public_connections(copy.deepcopy(value)), value)

    def test_both_relays_refuse_the_same_changes(self):
        for value in samples():
            for mutate in MUTATIONS:
                broken = copy.deepcopy(value)
                mutate(broken)
                with self.subTest(state=value["connectors"]["google"]["state"]):
                    with self.assertRaises(status.StatusError):
                        status.validate_public(copy.deepcopy(broken))
                    with self.assertRaises(ValueError):
                        edge.public_connections(copy.deepcopy(broken))


if __name__ == "__main__":
    unittest.main()
