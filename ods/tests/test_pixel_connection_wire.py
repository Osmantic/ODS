"""The Edge relay and the host agent accept exactly the same connected-apps status and actions."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_connections import status  # noqa: E402

SPEC = importlib.util.spec_from_file_location("edge_access_mode", ROOT / "extensions/services/pixel-edge/access_mode.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

OWNERS = (0, os.getuid()) if hasattr(os, "getuid") else (0,)
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
            (RECORD, False, None, journal("consent")), (None, False, None, journal("failed", "access-denied"))):
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
            if pending:
                (root / "connection.json").write_text(json.dumps(pending))
                os.chmod(root / "connection.json", 0o600)
            values.append(status.report(surface="wsl-systemd", record_path=root / "record.json",
                                        token_path=root / "token.json", status_path=root / "status.json",
                                        lifecycle_path=root / "connection.json", now=NOW, owners=OWNERS))
    return values


MUTATIONS = (
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
    ("connection-begin", dict(BEGIN, ownerTimeZone=None, loginHint="owner@example.com", cloudModels=True)),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "code": "4/0AfJohX-fixture"}),
    ("connection-complete", {"flowId": FLOW, "state": "s" * 43, "error": "access-denied"}),
    ("connection-activate", {}), ("connection-cancel", {"flowId": FLOW}),
    ("connection-disconnect", {"connector": "google"}), ("connection-sync", {}),
)
BAD_REQUESTS = (
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
)
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
)
VALIDATORS = ("valid_connection_action", "public_connection_action", "_text", "_capability_flags")
CONSTANTS = ("_CONNECTION_OPERATIONS", "_CAPABILITY_IDS", "_FLOW", "_STATE", "_CODE", "_CLIENT_ID", "_CLIENT_SECRET",
             "_PROJECT", "_ACCOUNT", "_TIME_ZONE", "_SECOND", "_REASON", "_AUTH_URL")


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
