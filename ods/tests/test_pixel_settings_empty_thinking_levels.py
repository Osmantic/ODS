"""Verify _capabilities rejects empty supportedThinkingLevels list."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import _capabilities, SettingsError

class EmptyThinkingLevelsTests(unittest.TestCase):
    def test_empty_thinking_levels_rejected(self):
        caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 4096,
            "activeContextTokens": 8192,
            "activeMaxOutputTokens": 4096,
            "backendContextTokens": None,
            "capacitySource": "provider-declared",
            "supportedThinkingLevels": [],
            "samplingSupported": True,
            "pixelOnlyRuntime": True
        }
        with self.assertRaises(SettingsError) as ctx:
            _capabilities(caps)
        self.assertIn("invalid-runtime-capabilities", str(ctx.exception))

    def test_valid_thinking_levels_accepted(self):
        caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 4096,
            "activeContextTokens": 8192,
            "activeMaxOutputTokens": 4096,
            "backendContextTokens": None,
            "capacitySource": "provider-declared",
            "supportedThinkingLevels": ["off"],
            "samplingSupported": True,
            "pixelOnlyRuntime": True
        }
        res = _capabilities(caps)
        self.assertEqual(res["supportedThinkingLevels"], ["off"])

if __name__ == "__main__":
    unittest.main()
