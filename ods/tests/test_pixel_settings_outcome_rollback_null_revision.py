"""Verify normalize_outcome rejects non-null appliedRevision on rolled-back outcome."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError
from pixel_settings.public import normalize_outcome

class OutcomeRollbackNullRevisionTests(unittest.TestCase):
    def test_applied_revision_on_rollback_rejected(self):
        val = {"outcome": "rolled-back", "appliedRevision": 10}
        with self.assertRaises(SettingsError):
            normalize_outcome(val)

    def test_null_applied_revision_on_rollback_accepted(self):
        val = {"outcome": "rolled-back", "appliedRevision": None}
        res = normalize_outcome(val)
        self.assertEqual(res["outcome"], "rolled-back")
        self.assertIsNone(res["appliedRevision"])

if __name__ == "__main__":
    unittest.main()
