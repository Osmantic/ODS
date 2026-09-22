"""Verify ChatResultStore append rejects non-bytes chunk data."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import ChatResultStore

class ChatResultsAppendBytesTypeTests(unittest.TestCase):
    def test_non_bytes_chunk_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatResultStore(Path(tmp).resolve())
            key = ("owner1", "chat1", "attempt1")
            for bad in ("string", None, 123, []):
                with self.assertRaises(TypeError) as ctx:
                    store.append(key, bad)
                self.assertEqual(str(ctx.exception), "Chunk data must be bytes")
            store.close()

if __name__ == "__main__":
    unittest.main()
