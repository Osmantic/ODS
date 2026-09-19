"""token-spy tool filter must drop malformed tool schemas missing names."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/token-spy"))
from filters import apply_filters

class TokenSpyUnnamedToolTests(unittest.TestCase):
    def test_unnamed_tool_dropped(self):
        body = {
            "tools": [
                {"type": "function", "function": {"name": ""}},
                {"type": "function", "function": {"name": "valid_tool"}},
            ]
        }
        cfg = {"enabled": True, "tools": {"enabled": True, "mode": "blocklist", "blocklist": []}}
        filtered, res = apply_filters(body, cfg)
        self.assertEqual(len(filtered["tools"]), 1)
        self.assertEqual(filtered["tools"][0]["function"]["name"], "valid_tool")
        self.assertEqual(res.tools_removed, 1)

if __name__ == "__main__":
    unittest.main()
