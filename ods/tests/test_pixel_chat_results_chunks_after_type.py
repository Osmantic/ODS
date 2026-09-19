"""Verify ChatResultStore chunks rejects non-integer or negative < -1 after values."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class ChatResultsChunksAfterTypeTests(unittest.TestCase):
    def test_invalid_after_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatResultStore(Path(tmp).resolve())
            key = ("owner1", "chat1", "attempt1")
            for bad in (-2, "0", None, 1.5, True):
                with self.assertRaises(TypeError) as ctx:
                    store.chunks(key, after=bad)
                self.assertEqual(str(ctx.exception), "after must be an integer >= -1")
            store.close()

if __name__ == "__main__":
    unittest.main()
