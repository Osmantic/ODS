"""Verify asks_display_name handles non-string message content without raising."""
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_identity import asks_display_name

class ChatIdentityMultimodalContentTests(unittest.TestCase):
    def test_non_string_content_returns_false(self):
        for bad_content in (None, [{"type": "text", "text": "hello"}], 123):
            msg = SimpleNamespace(role="user", content=bad_content)
            self.assertFalse(asks_display_name([msg]))

if __name__ == "__main__":
    unittest.main()
