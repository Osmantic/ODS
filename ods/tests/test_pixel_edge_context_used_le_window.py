"""Verify project_context rejects used tokens exceeding context window."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import project_context

class ContextUsedLeWindowTests(unittest.TestCase):
    def test_used_exceeding_window_rejected(self):
        doc = {
            "schemaVersion": 1,
            "status": "ready",
            "context": {"used": 8192, "window": 4096, "measuredAt": "2026-09-19T12:00:00Z"},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 0}
        }
        with self.assertRaises(ValueError) as ctx:
            project_context(doc)
        self.assertEqual(str(ctx.exception), "invalid context measurement")

    def test_valid_used_accepted(self):
        doc = {
            "schemaVersion": 1,
            "status": "ready",
            "context": {"used": 2048, "window": 4096, "measuredAt": "2026-09-19T12:00:00Z"},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 0}
        }
        res = project_context(doc)
        self.assertEqual(res["context"]["used"], 2048)

if __name__ == "__main__":
    unittest.main()
