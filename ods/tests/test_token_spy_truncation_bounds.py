"""token-spy filter must not truncate on negative character thresholds."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/token-spy"))
from filters import apply_filters

class TokenSpyTruncationBoundsTests(unittest.TestCase):
    def test_negative_truncation_setting_ignored(self):
        body = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "f"}}]},
                {"role": "tool", "tool_call_id": "1", "content": "valid output"},
            ]
        }
        cfg = {"enabled": True, "history": {"enabled": True, "truncate_tool_results_chars": -1}}
        filtered, res = apply_filters(body, cfg)
        self.assertEqual(filtered["messages"][2]["content"], "valid output")
        self.assertEqual(res.tool_results_truncated, 0)

if __name__ == "__main__":
    unittest.main()
