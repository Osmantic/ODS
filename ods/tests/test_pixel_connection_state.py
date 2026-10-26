"""The owner's connected-apps record: strict schema, safe reads, onboarding mapping."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "ods_portal_connection_state", Path(__file__).resolve().parents[1] / "bin" / "pixel_connections" / "state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)

OWNERS = (0, os.getuid()) if hasattr(os, "getuid") else (0,)


def record(**google_changes):
    google = {
        "status": "connected",
        "account": "owner@example.com",
        "calendarId": "primary",
        "gmailWindowDays": 180,
        "capabilities": {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
                         "gmail.read": True, "gmail.drafts": False},
        "cloudModels": True,
        "connectedAt": "2026-10-25T20:00:00Z",
    }
    google.update(google_changes)
    return {"schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
            "ownerTimeZone": "America/Chicago", "connectors": {"google": google}}


class ConnectionRecordSchemaTests(unittest.TestCase):
    def test_a_complete_record_validates_unchanged(self):
        value = record()
        self.assertIs(state.validate(value), value)
        self.assertEqual(state.parse(json.dumps(value).encode()), value)

    def test_a_record_from_before_private_changes_is_not_paused(self):
        for value in (record(), record(directPaused=False), record(directPaused=True)):
            self.assertIs(state.validate(value), value)
        self.assertNotIn("directPaused", record()["connectors"]["google"])

    def test_a_record_without_connectors_or_time_zone_is_valid(self):
        value = record()
        value.update(connectors={}, ownerTimeZone=None)
        self.assertEqual(state.validate(value)["connectors"], {})

    def test_schema_violations_are_refused(self):
        def changed(update):
            value = record()
            update(value)
            return value

        cases = {
            "unknown top-level key": changed(lambda v: v.update(extra=True)),
            "wrong version": changed(lambda v: v.update(schemaVersion=2)),
            "wrong kind": changed(lambda v: v.update(kind="something-else")),
            "local timestamp": changed(lambda v: v.update(updatedAt="2026-10-25T20:00:00")),
            "impossible date": changed(lambda v: v.update(updatedAt="2026-13-40T20:00:00Z")),
            "unknown zone": changed(lambda v: v.update(ownerTimeZone="Mars/Olympus")),
            "zone region only": changed(lambda v: v.update(ownerTimeZone="America")),
            "zone path": changed(lambda v: v.update(ownerTimeZone="../../etc/passwd")),
            "unknown connector": changed(lambda v: v["connectors"].update(dropbox={})),
            "missing Google field": changed(lambda v: v["connectors"]["google"].pop("cloudModels")),
            "unknown Google field": changed(lambda v: v["connectors"]["google"].update(token="never-here")),
            "pending status": changed(lambda v: v["connectors"]["google"].update(status="pending")),
            "not an email": changed(lambda v: v["connectors"]["google"].update(account="owner")),
            "unsafe calendar id": changed(lambda v: v["connectors"]["google"].update(calendarId="a b")),
            "window too long": changed(lambda v: v["connectors"]["google"].update(gmailWindowDays=4000)),
            "window not an integer": changed(lambda v: v["connectors"]["google"].update(gmailWindowDays=True)),
            "missing capability": changed(lambda v: v["connectors"]["google"]["capabilities"].pop("gmail.drafts")),
            "unknown capability": changed(lambda v: v["connectors"]["google"]["capabilities"].update(send=True)),
            "non-boolean capability": changed(lambda v: v["connectors"]["google"]["capabilities"].update({"gmail.read": 1})),
            "non-boolean cloud switch": changed(lambda v: v["connectors"]["google"].update(cloudModels="yes")),
            "non-boolean pause": changed(lambda v: v["connectors"]["google"].update(directPaused="yes")),
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaises(state.StateError):
                    state.validate(value)

    def test_parse_refuses_duplicate_keys_constants_and_non_utf8(self):
        for raw in (b'{"schemaVersion": 1, "schemaVersion": 1}', b'{"updatedAt": NaN}', b"\xff\xfe", b"[]",
                    b"x" * (state.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:24]):
                with self.assertRaises(state.StateError):
                    state.parse(raw)


class ConnectionRecordReadTests(unittest.TestCase):
    def write(self, directory, value, mode=0o640):
        path = Path(directory) / "portal-connections.json"
        path.write_bytes(json.dumps(value).encode())
        os.chmod(path, mode)
        return path

    def test_no_record_means_nothing_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            self.assertIsNone(state.read(Path(directory) / "portal-connections.json", owners=OWNERS))

    def test_a_protected_record_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            self.assertEqual(state.read(self.write(directory, record()), owners=OWNERS), record())

    @unittest.skipIf(os.name == "nt", "POSIX ownership and modes")
    def test_records_other_accounts_could_change_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            path = self.write(directory, record(), mode=0o664)
            with self.assertRaises(state.StateError):
                state.read(path, owners=OWNERS)
            os.chmod(path, 0o640)
            with self.assertRaises(state.StateError):
                state.read(path, owners=(0,) if os.getuid() else (12345,))
            os.chmod(directory, 0o777)
            with self.assertRaises(state.StateError):
                state.read(path, owners=OWNERS)

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_a_linked_record_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            target = self.write(directory, record())
            link = Path(directory) / "linked.json"
            link.symlink_to(target)
            with self.assertRaises(state.StateError):
                state.read(link, owners=OWNERS)
            target.unlink()
            with self.assertRaises(state.StateError):
                state.read(link, owners=OWNERS)

    def test_a_malformed_record_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            path = Path(directory) / "portal-connections.json"
            path.write_bytes(b"{not json")
            os.chmod(path, 0o640)
            with self.assertRaises(state.StateError):
                state.read(path, owners=OWNERS)


class OnboardingMappingTests(unittest.TestCase):
    def test_nothing_connected_changes_nothing(self):
        self.assertEqual(state.onboarding_overrides(None, google_custody=True), {})
        empty = record()
        empty.update(connectors={}, ownerTimeZone=None)
        self.assertEqual(state.onboarding_overrides(empty, google_custody=True), {})

    def test_a_connected_account_turns_on_exactly_its_capabilities(self):
        self.assertEqual(state.onboarding_overrides(record(), google_custody=True), {
            "timeZone": "America/Chicago", "userTimezone": "America/Chicago",
            "emailLimbEnabled": True, "emailDraftsEnabled": False, "calendarLimbEnabled": True,
            "calendarWriteEnabled": False, "calendarDirectEnabled": False,
            "googleAccount": "owner@example.com", "calendarId": "primary", "gmailWindowDays": 180,
        })

    def test_gmail_drafts_need_gmail_read(self):
        capabilities = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
                        "gmail.read": False, "gmail.drafts": True}
        overrides = state.onboarding_overrides(record(capabilities=dict(capabilities)), google_custody=True)
        self.assertEqual((overrides["emailLimbEnabled"], overrides["emailDraftsEnabled"]), (False, False))
        capabilities["gmail.read"] = True
        overrides = state.onboarding_overrides(record(capabilities=dict(capabilities)), google_custody=True)
        self.assertEqual((overrides["emailLimbEnabled"], overrides["emailDraftsEnabled"]), (True, True))
        # Nothing connected leaves the answer out, so a disconnected render keeps its bytes.
        answers = state.connection_answers(None, google_custody=False)
        self.assertIsNone(answers["emailDraftsEnabled"])
        self.assertTrue(state.valid_connection_answers(answers))
        self.assertFalse(state.valid_connection_answers(dict(answers, emailDraftsEnabled="yes")))

    def test_calendar_writes_need_reads_and_direct_writes_need_writes(self):
        capabilities = {"calendar.read": False, "calendar.write": True, "calendar.direct": True,
                        "gmail.read": False, "gmail.drafts": False}
        overrides = state.onboarding_overrides(record(capabilities=dict(capabilities)), google_custody=True)
        self.assertEqual((overrides["calendarLimbEnabled"], overrides["calendarWriteEnabled"],
                          overrides["calendarDirectEnabled"]), (False, False, False))
        capabilities.update({"calendar.read": True, "calendar.direct": False})
        overrides = state.onboarding_overrides(record(capabilities=dict(capabilities)), google_custody=True)
        self.assertEqual((overrides["calendarWriteEnabled"], overrides["calendarDirectEnabled"]), (True, False))
        capabilities["calendar.direct"] = True
        overrides = state.onboarding_overrides(record(capabilities=dict(capabilities)), google_custody=True)
        self.assertTrue(overrides["calendarDirectEnabled"])

    def test_full_access_pauses_private_changes_but_keeps_the_choice(self):
        chosen = {"calendar.read": True, "calendar.write": True, "calendar.direct": True,
                  "gmail.read": True, "gmail.drafts": False}
        paused = record(capabilities=dict(chosen), directPaused=True)
        overrides = state.onboarding_overrides(paused, google_custody=True)
        self.assertEqual((overrides["calendarWriteEnabled"], overrides["calendarDirectEnabled"]), (True, False))
        self.assertTrue(state.effective_capabilities(record(capabilities=dict(chosen))["connectors"]["google"])["calendar.direct"])
        self.assertFalse(state.effective_capabilities(paused["connectors"]["google"])["calendar.direct"])
        self.assertTrue(paused["connectors"]["google"]["capabilities"]["calendar.direct"], "the owner's choice stays")

    def test_a_connection_without_its_sign_in_keeps_sources_off(self):
        overrides = state.onboarding_overrides(record(), google_custody=False)
        self.assertEqual(overrides, {"timeZone": "America/Chicago", "userTimezone": "America/Chicago"})

    def test_mapping_does_not_change_the_record(self):
        value = record()
        before = copy.deepcopy(value)
        state.onboarding_overrides(value, google_custody=True)
        self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
