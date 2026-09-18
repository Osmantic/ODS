"""Verify ChatResultStore.reserve validates key elements are non-empty strings."""
import tempfile
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class PixelChatEmptyKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatResultStore(Path(self.tmp.name).resolve())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_empty_key_elements_rejected(self):
        for bad_key in (("", "chat1", "att1"), ("own1", "", "att1"), ("own1", "chat1", ""), ("own1", "chat1")):
            with self.assertRaises(ValueError):
                self.store.reserve(bad_key, "fp1")

    def test_valid_key_reserved(self):
        reserved = self.store.reserve(("owner1", "chat1", "attempt1"), "fp1")
        self.assertTrue(reserved)

if __name__ == "__main__":
    unittest.main()
