"""Verify ChatResultStore reserve rejects malformed attempt keys."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class ChatResultsKeyValidationTests(unittest.TestCase):
    def test_malformed_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatResultStore(Path(tmp).resolve())
            for bad_key in (("a", "b"), ("a", "b", "c", "d"), ("a", "", "c"), [1, 2, 3], None):
                with self.assertRaises(ValueError) as ctx:
                    store.reserve(bad_key, "fp1")
                self.assertEqual(str(ctx.exception), "Invalid attempt key")
            store.close()

if __name__ == "__main__":
    unittest.main()
