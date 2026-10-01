import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_authority import ManagedFullAccessPolicy, read_verified_access


class ProjectAuthorityTests(unittest.TestCase):
    def response(self, body, uid=0):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        stream = io.BytesIO(json.dumps({"status": 200, "body": body}).encode() + b"\n")
        connection.makefile.return_value = stream
        with patch("project_authority.socket.socket", return_value=connection), patch("project_authority.peer_ids", return_value=(uid, 0)):
            return read_verified_access()

    def test_requires_effective_proof_not_just_saved_mode(self):
        valid = {"available": True, "configured_mode": "full-access", "effective_mode": "full-access", "runtime_verified": True}
        self.assertTrue(self.response(valid))
        for changes in ({"effective_mode": "unknown"}, {"runtime_verified": False},
                        {"configured_mode": "sandboxed"}, {"available": False}):
            self.assertFalse(self.response({**valid, **changes}))
        with self.assertRaises(PermissionError):
            self.response(valid, uid=1000)

    def test_revocation_and_outage_fail_closed_but_allow_stop(self):
        policy = ManagedFullAccessPolicy()
        with patch("project_authority.read_verified_access", side_effect=[True, False, OSError("offline")]) as read:
            self.assertTrue(policy("project", "execute"))
            self.assertFalse(policy("project", "import"))
            self.assertFalse(policy("project", "execute"))
            self.assertTrue(policy("project", "cancel"))
            self.assertTrue(policy("project", "observe"))
            self.assertFalse(policy("project", "unknown"))
            self.assertEqual(read.call_count, 3)
