"""Verify public_context rejects used tokens exceeding context window."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_context import public_context

class ChatContextUsedExceedsWindowTests(unittest.TestCase):
    def test_used_exceeding_window_rejected(self):
        doc = {
            "schemaVersion": 1,
            "status": "ready",
            "sessionRevision": "rev-1",
            "context": {"used": 16000, "window": 8192, "measuredAt": "2026-09-19T12:00:00Z"},
            "model": {"id": "qwen2.5", "provider": "ods-local", "contextWindow": 8192},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 0}
        }
        with self.assertRaises(ValueError) as ctx:
            public_context(doc)
        self.assertEqual(str(ctx.exception), "Context measurement exceeds context window")

if __name__ == "__main__":
    unittest.main()
