"""Verify _capabilities rejects activeMaxOutputTokens > providerMaxOutputTokens."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import _capabilities, SettingsError

class PixelSettingsOutputBoundTests(unittest.TestCase):
    def setUp(self):
        self.base_caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 2048,
            "activeContextTokens": 4096,
            "activeMaxOutputTokens": 2048,
            "backendContextTokens": 8192,
            "capacitySource": "backend-observed",
            "supportedThinkingLevels": ["low"],
            "samplingSupported": True,
            "pixelOnlyRuntime": True,
        }

    def test_valid_active_output_tokens(self):
        caps = _capabilities(self.base_caps)
        self.assertEqual(caps["activeMaxOutputTokens"], 2048)

    def test_active_output_exceeding_provider_rejected(self):
        bad = dict(self.base_caps, activeMaxOutputTokens=4096)  # > providerMaxOutputTokens 2048
        with self.assertRaises(SettingsError):
            _capabilities(bad)

if __name__ == "__main__":
    unittest.main()
