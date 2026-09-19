"""Verify preview_preferences allows thinking='off' when provider omits 'off'."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import preview_preferences, SettingsError

class PixelSettingsThinkingOffTests(unittest.TestCase):
    def setUp(self):
        self.caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 2048,
            "activeContextTokens": 8192,
            "activeMaxOutputTokens": 2048,
            "backendContextTokens": 8192,
            "capacitySource": "backend-observed",
            "supportedThinkingLevels": ["low", "medium", "high"],  # 'off' omitted
            "samplingSupported": True,
            "pixelOnlyRuntime": True,
        }

    def test_thinking_off_permitted(self):
        preview = preview_preferences({"thinking": "off"}, self.caps)
        self.assertEqual(preview["proposed"]["thinking"], "off")

    def test_unsupported_thinking_level_rejected(self):
        with self.assertRaises(SettingsError) as ctx:
            preview_preferences({"thinking": "xhigh"}, self.caps)
        self.assertEqual(str(ctx.exception), "thinking-level-not-supported")

if __name__ == "__main__":
    unittest.main()
