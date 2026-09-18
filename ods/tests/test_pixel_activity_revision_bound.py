"""Verify transition_decision enforces safe JavaScript integer upper bound on revisions."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.activity import transition_decision, ActivitySnapshot

class ActivityRevisionBoundTests(unittest.TestCase):
    def test_oversized_revisions_rejected(self):
        snap = ActivitySnapshot("idle", (), "")
        for bad_rev in (2**53, 2**60, -1):
            res = transition_decision(snap, expected_revision=bad_rev, current_revision=0, requested_mode="sandboxed", confirmed=False)
            self.assertFalse(res["allowed"])
            self.assertEqual(res["reason"], "invalid-request")

    def test_valid_revision_admitted(self):
        snap = ActivitySnapshot("idle", (), "")
        res = transition_decision(snap, expected_revision=10, current_revision=10, requested_mode="sandboxed", confirmed=False)
        self.assertTrue(res["allowed"])
        self.assertEqual(res["reason"], "ready")

if __name__ == "__main__":
    unittest.main()
