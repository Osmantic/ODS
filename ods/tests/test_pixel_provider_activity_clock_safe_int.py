"""Verify assess_activity rejects clock values exceeding JavaScript MAX_SAFE_INTEGER."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.activity import assess_activity, REASON_INVALID_CLOCK

class ActivityClockSafeIntTests(unittest.TestCase):
    def test_clock_exceeding_safe_int_rejected(self):
        doc = {"schemaVersion": 1, "observedAt": 1000, "sourceEpoch": "epoch-1", "runs": []}
        res1 = assess_activity(doc, now_ms=2**53, max_age_ms=5000, expected_epoch="epoch-1")
        self.assertEqual(res1.reason, REASON_INVALID_CLOCK)

        res2 = assess_activity(doc, now_ms=1000, max_age_ms=2**53, expected_epoch="epoch-1")
        self.assertEqual(res2.reason, REASON_INVALID_CLOCK)

if __name__ == "__main__":
    unittest.main()
