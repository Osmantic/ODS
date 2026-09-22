"""Verify public_context rejects tokensAfter exceeding tokensBefore on completed compaction."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_context import public_context

class ChatContextCompactionTokensOrderTests(unittest.TestCase):
    def test_expanded_tokens_on_completed_compaction_rejected(self):
        doc = {
            "schemaVersion": 1,
            "status": "ready",
            "sessionRevision": "rev-1",
            "context": None,
            "model": None,
            "compaction": {
                "status": "completed",
                "count": 1,
                "tokensBefore": 2048,
                "tokensAfter": 4096
            },
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 0}
        }
        with self.assertRaises(ValueError) as ctx:
            public_context(doc)
        self.assertEqual(str(ctx.exception), "Compaction tokens after cannot exceed tokens before")

if __name__ == "__main__":
    unittest.main()
