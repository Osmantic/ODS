"""Verify assess_activity rejects observedAt exceeding MAX_SAFE_INTEGER."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.activity import assess_activity, REASON_MALFORMED_SOURCE

class ActivityObservedAtSafeIntTests(unittest.TestCase):
    def test_observed_at_exceeding_safe_int_rejected(self):
        doc = {"schemaVersion": 1, "observedAt": 2**53, "sourceEpoch": "epoch-1", "runs": []}
        res = assess_activity(doc, now_ms=1000, max_age_ms=5000, expected_epoch="epoch-1")
        self.assertEqual(res.reason, REASON_MALFORMED_SOURCE)

if __name__ == "__main__":
    unittest.main()
