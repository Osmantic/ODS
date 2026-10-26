"""The Edge relay and the host agent accept exactly the same connected-apps status."""
import copy
import importlib.util
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


def samples():
    values = [status.unavailable("darwin"), status.unavailable("windows")]
    for record, token, broker_value in ((None, False, None), (RECORD, False, None), (RECORD, True, None),
                                        (RECORD, True, broker()), (RECORD, True, broker("network")),
                                        (RECORD, True, broker("invalid-grant"))):
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
            values.append(status.report(surface="wsl-systemd", record_path=root / "record.json",
                                        token_path=root / "token.json", status_path=root / "status.json",
                                        now=NOW, owners=OWNERS))
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
)


class WireContractTests(unittest.TestCase):
    def test_both_relays_accept_every_status_the_controller_produces(self):
        for value in samples():
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
