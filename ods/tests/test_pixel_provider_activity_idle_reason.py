"""Verify transition_decision rejects non-empty error reason for idle or busy snapshots."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.activity import ActivitySnapshot, transition_decision

class ActivityIdleReasonTests(unittest.TestCase):
    def test_idle_with_error_reason_rejected(self):
        snap = ActivitySnapshot("idle", (), "malformed-source")
        res = transition_decision(
            snap,
            expected_revision=1,
            current_revision=1,
            requested_mode="sandboxed",
            confirmed=True
        )
        self.assertFalse(res["allowed"])
        self.assertEqual(res["reason"], "invalid-request")

    def test_clean_idle_snapshot_accepted(self):
        snap = ActivitySnapshot("idle", (), "")
        res = transition_decision(
            snap,
            expected_revision=1,
            current_revision=1,
            requested_mode="sandboxed",
            confirmed=True
        )
        self.assertTrue(res["allowed"])
        self.assertEqual(res["reason"], "ready")

if __name__ == "__main__":
    unittest.main()
