"""Verify _capabilities enforces minimum contextTokens bound of 4096."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import _capabilities, SettingsError

class SettingsMinContextBoundTests(unittest.TestCase):
    def test_undersized_context_rejected(self):
        caps = {
            "providerContextTokens": 1000,
            "providerMaxOutputTokens": 500,
            "activeContextTokens": 1000,
            "activeMaxOutputTokens": 500,
            "backendContextTokens": None,
            "capacitySource": "provider-declared",
            "supportedThinkingLevels": ["off"],
            "samplingSupported": True,
            "pixelOnlyRuntime": True
        }
        with self.assertRaises(SettingsError):
            _capabilities(caps)

    def test_valid_context_accepted(self):
        caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 2048,
            "activeContextTokens": 8192,
            "activeMaxOutputTokens": 2048,
            "backendContextTokens": None,
            "capacitySource": "provider-declared",
            "supportedThinkingLevels": ["off"],
            "samplingSupported": True,
            "pixelOnlyRuntime": True
        }
        res = _capabilities(caps)
        self.assertEqual(res["providerContextTokens"], 8192)

if __name__ == "__main__":
    unittest.main()
