"""Verify project_context rejects trailing hyphens in compaction reason identifiers."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import project_context

class CompactionReasonPatternTests(unittest.TestCase):
    def test_trailing_hyphen_reason_rejected(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "compaction": {"status": "idle", "count": 0, "reason": "overflow-"},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        with self.assertRaises(ValueError):
            project_context(val)

    def test_valid_reason_accepted(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "compaction": {"status": "idle", "count": 0, "reason": "token-overflow"},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        res = project_context(val)
        self.assertEqual(res["compaction"]["reason"], "token-overflow")

if __name__ == "__main__":
    unittest.main()
