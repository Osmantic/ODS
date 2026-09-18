"""Verify project_context validates used tokens cannot exceed context window."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import project_context

class ContextUsedBoundTests(unittest.TestCase):
    def test_used_exceeding_window_rejected(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "context": {"used": 5000, "window": 4096, "measuredAt": "2026-09-18T00:00:00Z"},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        with self.assertRaises(ValueError):
            project_context(val)

    def test_used_within_window_accepted(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "context": {"used": 1024, "window": 4096, "measuredAt": "2026-09-18T00:00:00Z"},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        res = project_context(val)
        self.assertEqual(res["context"]["used"], 1024)

if __name__ == "__main__":
    unittest.main()
