"""Connected-apps status: one owner-facing state per connector, and nothing private."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_connections import catalog, lifecycle, status  # noqa: E402

OWNERS = (0, os.getuid()) if hasattr(os, "getuid") else (0,)
NOW = datetime(2026, 10, 25, 20, 30, tzinfo=timezone.utc)
CALENDAR_READ = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_WRITE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"


def record(**capabilities):
    flags = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False, **capabilities}
    return {"schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
            "ownerTimeZone": "America/Chicago",
            "connectors": {"google": {
                "status": "connected", "account": "owner@example.com", "calendarId": "primary",
                "gmailWindowDays": 180, "capabilities": flags, "cloudModels": True,
                "connectedAt": "2026-10-25T20:00:00Z"}}}


def source(state="ok", error_class=None, **extra):
    entry = {"enabled": state != "disabled", "state": state, "errorClass": error_class,
             "httpStatus": 403 if error_class == "api-disabled" else None, "errorType": None,
             "lastAttemptAt": "2026-10-25T20:29:00Z", "lastSuccessAt": "2026-10-25T20:20:00Z",
             "failingSince": "2026-10-25T20:21:00Z" if state == "error" else None,
             "recordCount": 12, "complete": True}
    entry.update(extra)
    return entry


def broker(email=None, calendar=None, generated="2026-10-25T20:29:30Z", **extra):
    value = {"schemaVersion": 1, "kind": "pixel-source-broker-status", "startedAt": generated,
             "generatedAt": generated, "grantedScopes": [CALENDAR_READ, GMAIL_READ],
             "sources": {"email": email or source(), "calendar": calendar or source(),
                         "social": source("disabled")},
             "boundary": {"contentFree": True, "credentialsIncluded": False}}
    value.update(extra)
    return value


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)
        os.chmod(self.root, 0o755)
        self.record = self.root / "portal-connections.json"
        self.token = self.root / "google-token.json"
        self.broker = self.root / "status.json"
        self.lifecycle = self.root / "connection.json"

    def write(self, *, record_value=None, token=False, broker_value=None, raw_broker=None):
        if record_value is not None:
            self.record.write_text(record_value if isinstance(record_value, str) else json.dumps(record_value))
            os.chmod(self.record, 0o640)
        if token:
            self.token.write_text("{}")
        if broker_value is not None:
            self.broker.write_text(json.dumps(broker_value))
        if raw_broker is not None:
            self.broker.write_bytes(raw_broker)
        return self

    def report(self, surface="linux-systemd", now=NOW):
        value = status.report(surface=surface, record_path=self.record, token_path=self.token,
                              status_path=self.broker, lifecycle_path=self.lifecycle, now=now, owners=OWNERS,
                              broker_state=self.root / "broker")
        status.validate_public(value)
        return value


class ConnectionStatusTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Fixture(directory.name)

    def google(self, value):
        return value["connectors"]["google"]

    def test_other_systems_say_connected_apps_are_not_available_yet(self):
        for surface in ("darwin", "windows", "linux"):
            with self.subTest(surface=surface):
                value = self.fixture().write(record_value=record(), token=True, broker_value=broker()).report(surface)
                self.assertFalse(value["available"])
                self.assertEqual(value["reason"], "not-available-on-this-system")
                self.assertEqual(self.google(value)["state"], "not-connected")

    def test_nothing_connected_is_reported_as_not_connected(self):
        value = self.fixture().report()
        self.assertTrue(value["available"])
        self.assertIsNone(value["reason"])
        self.assertEqual(self.google(value), status.not_connected())
        self.assertIsNone(value["ownerTimeZone"])

    def test_an_unusable_record_offers_to_reconnect(self):
        value = self.fixture().write(record_value="{not json").report()
        self.assertEqual((value["available"], value["reason"]), (True, "record-unusable"))
        self.assertEqual(self.google(value)["state"], "not-connected")

    def test_a_connection_whose_sign_in_is_missing_needs_reconnecting(self):
        google = self.google(self.fixture().write(record_value=record(), broker_value=broker()).report())
        self.assertEqual((google["state"], google["reason"]), ("needs-reconnect", "sign-in-missing"))
        self.assertEqual(google["account"], "owner@example.com")
        self.assertEqual(google["sources"], {"email": None, "calendar": None})

    def test_a_new_connection_is_syncing_until_the_broker_reports(self):
        google = self.google(self.fixture().write(record_value=record(), token=True).report())
        self.assertEqual((google["state"], google["checkedAt"]), ("first-sync", None))

    def test_a_healthy_connection_reports_each_source_without_content(self):
        sensitive = broker(email=source(summary="Lunch with Sam", account="owner@example.com"),
                           extra_field="private")
        value = self.fixture().write(record_value=record(), token=True, broker_value=sensitive).report()
        google = self.google(value)
        self.assertEqual(google["state"], "connected")
        self.assertEqual(google["checkedAt"], "2026-10-25T20:29:30Z")
        self.assertEqual(google["grantedScopes"], [CALENDAR_READ, GMAIL_READ])
        self.assertEqual(set(google["sources"]["email"]), status.SOURCE_KEYS)
        self.assertEqual(value["ownerTimeZone"], "America/Chicago")
        text = json.dumps(value)
        for private in ("Lunch with Sam", "extra_field", "social", "errorType", "lastAttemptAt"):
            self.assertNotIn(private, text)

    def test_each_error_class_maps_to_the_fix_the_owner_needs(self):
        cases = {
            "invalid-grant": "needs-reconnect", "not-connected": "needs-reconnect",
            "blocked-by-admin": "blocked-by-admin", "api-disabled": "setup-broken",
            "client-invalid": "setup-broken", "missing-scope": "missing-permission",
            "unknown": "unknown-error", "rate-limited": "behind", "network": "behind",
        }
        for error_class, expected in cases.items():
            with self.subTest(error_class=error_class):
                failing = broker(calendar=source("error", error_class))
                google = self.google(self.fixture().write(record_value=record(), token=True,
                                                          broker_value=failing).report())
                self.assertEqual((google["state"], google["reason"]), (expected, error_class))
                self.assertEqual(google["sources"]["calendar"]["errorClass"], error_class)

    def test_the_most_actionable_fix_wins(self):
        failing = broker(email=source("error", "network"), calendar=source("error", "invalid-grant"))
        google = self.google(self.fixture().write(record_value=record(), token=True, broker_value=failing).report())
        self.assertEqual(google["state"], "needs-reconnect")

    def test_errors_in_a_source_the_owner_did_not_turn_on_are_ignored(self):
        failing = broker(email=source("error", "api-disabled"))
        google = self.google(self.fixture().write(record_value=record(**{"gmail.read": False}), token=True,
                                                  broker_value=failing).report())
        self.assertEqual(google["state"], "connected")

    def test_a_source_the_broker_has_not_started_is_still_syncing(self):
        waiting = broker(calendar=source("disabled"))
        google = self.google(self.fixture().write(record_value=record(), token=True, broker_value=waiting).report())
        self.assertEqual(google["state"], "first-sync")

    def test_a_broker_that_stopped_refreshing_is_behind(self):
        google = self.google(self.fixture().write(record_value=record(), token=True, broker_value=broker())
                             .report(now=NOW + timedelta(hours=1)))
        self.assertEqual((google["state"], google["reason"]), ("behind", "sync-stopped"))

    def test_an_unreadable_broker_status_is_an_unknown_error(self):
        for raw in (b"{not json", json.dumps(broker(kind="other")).encode(),
                    json.dumps(broker(calendar={"state": "exploded"})).encode()):
            with self.subTest(raw=raw[:30]):
                google = self.google(self.fixture().write(record_value=record(), token=True, raw_broker=raw).report())
                self.assertEqual((google["state"], google["reason"]), ("unknown-error", "status-unreadable"))
                self.assertEqual(google["sources"], {"email": None, "calendar": None})

    def test_the_public_shape_is_enforced(self):
        value = self.fixture().write(record_value=record(), token=True, broker_value=broker()).report()
        status.validate_public(value)
        for change in (
            lambda v: v.update(extra=True),
            lambda v: v.update(surface="android"),
            lambda v: v.update(ownerTimeZone="not a zone"),
            lambda v: v["connectors"].update(dropbox=None),
            lambda v: v["connectors"]["google"].update(state="partying"),
            lambda v: v["connectors"]["google"].update(account=None),
            lambda v: v["connectors"]["google"]["sources"]["email"].update(summary="Lunch"),
            lambda v: v["connectors"]["google"]["sources"]["email"].update(recordCount=-1),
            lambda v: v["connectors"]["google"]["capabilities"].update(send=True),
        ):
            broken = copy.deepcopy(value)
            change(broken)
            with self.assertRaises(status.StatusError):
                status.validate_public(broken)

    @unittest.skipIf(os.name == "nt", "POSIX ownership and links")
    def test_a_step_in_progress_is_shown_and_an_unusable_journal_never_hides_the_connection(self):
        fixture = self.fixture().write(record_value=record(), token=True, broker_value=broker())
        journal = {"kind": "connection", "connector": "google", "phase": "activating", "flowId": None,
                   "stateHash": None, "redirectUri": None, "expiresAt": None, "request": None,
                   "error": "runtime-busy", "updatedAt": "2026-10-25T20:29:00Z"}
        lifecycle.write(fixture.lifecycle, journal)
        self.assertEqual(self.google(fixture.report())["pending"], {
            "action": "connect", "phase": "activating", "expiresAt": None, "error": "runtime-busy",
            "since": "2026-10-25T20:29:00Z"})
        for name, plant in (("not json", lambda path: path.write_text("{not json")),
                            ("a planted link", lambda path: path.symlink_to(fixture.record)),
                            ("readable by others", lambda path: (path.write_text(json.dumps(journal)),
                                                                 path.chmod(0o644)))):
            with self.subTest(name):
                fixture.lifecycle.unlink()
                plant(fixture.lifecycle)
                value = fixture.report()
                self.assertEqual(value["reason"], "journal-unusable")
                self.assertEqual(value["connectors"]["google"]["state"], "connected")
                self.assertIsNone(value["connectors"]["google"]["pending"])


class WaitingChangeTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "connected apps run under Linux and WSL")
    def test_waiting_calendar_changes_are_counted_and_never_shown(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fixture = Fixture(directory.name).write(record_value=record(**{"calendar.write": True}), token=True,
                                                broker_value=broker())
        proposals, results = fixture.root / "broker" / "proposals", fixture.root / "broker" / "results"
        proposals.mkdir(parents=True)
        results.mkdir()
        waiting = ({"status": "pending-operator-approval", "values": {"summary": "Lunch with Sam"}},
                   {"status": "pending-bounded-direct"}, "{not json", {"status": "pending-operator-approval"})
        for index, value in enumerate(waiting):
            path = proposals / f"calendar-17615000000{index:02d}-0badc0de.json"
            path.write_text(value if isinstance(value, str) else json.dumps(value))
            os.chmod(path, 0o640)
        (proposals / "notes.json").write_text("{}")
        settled = results / "calendar-1761500000003-0badc0de.json"
        settled.write_text("{}")
        os.chmod(settled, 0o640)
        value = fixture.report()
        # The suggestion and the unreadable file wait; Pixel makes the direct one, and one is settled.
        self.assertEqual(value["connectors"]["google"]["waitingChanges"], 2)
        self.assertNotIn("Lunch with Sam", json.dumps(value))
        fixture.record.unlink()
        self.assertIsNone(fixture.report()["connectors"]["google"]["waitingChanges"], "nothing connected, no count")


class CatalogTests(unittest.TestCase):
    def test_scopes_follow_only_the_available_capabilities_the_owner_chose(self):
        chosen = {"calendar.read": True, "gmail.read": True, "calendar.write": True, "gmail.drafts": True}
        self.assertEqual(catalog.scopes_for("google", chosen), [CALENDAR_READ, CALENDAR_WRITE, GMAIL_READ])
        self.assertEqual(catalog.scopes_for("google", {"calendar.read": True}), [CALENDAR_READ])
        self.assertEqual(catalog.scopes_for("google", {}), [])

    def test_the_public_catalog_is_a_copy(self):
        copied = catalog.public_catalog()
        copied[0]["capabilities"].clear()
        self.assertTrue(catalog.GOOGLE["capabilities"])

    def test_capabilities_match_the_connection_record(self):
        from pixel_connections import state
        self.assertEqual([item["id"] for item in catalog.GOOGLE["capabilities"]], list(state.GOOGLE_CAPABILITIES))
        for capability in catalog.GOOGLE["capabilities"]:
            self.assertTrue(set(capability["requires"]) <= set(state.GOOGLE_CAPABILITIES))


if __name__ == "__main__":
    unittest.main()
