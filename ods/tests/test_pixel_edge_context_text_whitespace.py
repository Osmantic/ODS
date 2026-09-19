"""Verify _text and project_context reject strings with surrounding whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import project_context, _text

class ContextTextWhitespaceTests(unittest.TestCase):
    def test_unstripped_text_rejected(self):
        for bad in ("  qwen2.5  ", " qwen2.5", "qwen2.5 "):
            self.assertFalse(_text(bad, 512))

    def test_unstripped_model_in_project_context_rejected(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "model": {"id": "  qwen2.5  ", "provider": "ods", "contextWindow": 8192},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        with self.assertRaises(ValueError):
            project_context(val)

    def test_clean_model_accepted(self):
        val = {
            "schemaVersion": 1,
            "status": "ready",
            "model": {"id": "qwen2.5:7b", "provider": "ods", "contextWindow": 8192},
            "compaction": {"status": "idle", "count": 0},
            "history": {"status": "ready", "revision": None, "acknowledgedMessages": 1}
        }
        res = project_context(val)
        self.assertEqual(res["model"]["id"], "qwen2.5:7b")

if __name__ == "__main__":
    unittest.main()
