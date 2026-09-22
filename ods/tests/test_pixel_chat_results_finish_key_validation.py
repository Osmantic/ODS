"""Verify ChatResultStore finish rejects malformed attempt keys."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class ChatResultsFinishKeyValidationTests(unittest.TestCase):
    def test_malformed_key_in_finish_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatResultStore(Path(tmp).resolve())
            for bad_key in (("a",), ("a", "b", "c", "d"), None, [1, 2, 3]):
                with self.assertRaises(ValueError) as ctx:
                    store.finish(bad_key, "complete")
                self.assertEqual(str(ctx.exception), "Invalid attempt key")
            store.close()

if __name__ == "__main__":
    unittest.main()
