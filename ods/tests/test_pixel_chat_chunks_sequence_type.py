"""Verify ChatResultStore.chunks validates sequence number type."""
import tempfile
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class PixelChatChunksSequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatResultStore(Path(self.tmp.name).resolve())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_invalid_after_type_rejected(self):
        for bad in ("0", True, False, None, 1.5):
            with self.assertRaises(ValueError):
                self.store.chunks(("own", "chat", "att"), after=bad)

    def test_valid_after_accepted(self):
        chunks = self.store.chunks(("own", "chat", "att"), after=0)
        self.assertEqual(chunks, [])

if __name__ == "__main__":
    unittest.main()
