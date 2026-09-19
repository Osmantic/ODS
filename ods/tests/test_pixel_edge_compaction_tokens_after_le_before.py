"""Verify project_context rejects tokensAfter > tokensBefore on completed compaction."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import project_context

class CompactionTokensAfterLeBeforeTests(unittest.TestCase):
    def test_expansion_on_completed_compaction_rejected(self):
        doc = {
            "schemaVersion": 1,
            "status": "ready",
            "compaction": {
                "status": "completed",
                "count": 1,
                "tokensBefore": 1000,
                "tokensAfter": 2000
            },
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 0}
        }
        with self.assertRaises(ValueError) as ctx:
            project_context(doc)
        self.assertEqual(str(ctx.exception), "invalid compaction detail")

if __name__ == "__main__":
    unittest.main()
