"""pixel settings capabilities contract must reject active context exceeding provider context."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_settings.contract import _capabilities, SettingsError

class PixelSettingsCapabilitiesTests(unittest.TestCase):
    def test_active_context_exceeding_provider_fails(self):
        caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 2048,
            "activeContextTokens": 16384,  # Exceeds provider context!
            "activeMaxOutputTokens": 2048,
            "backendContextTokens": None,
            "capacitySource": "provider-declared",
            "supportedThinkingLevels": ["off"],
            "samplingSupported": True,
            "pixelOnlyRuntime": False,
        }
        with self.assertRaises(SettingsError):
            _capabilities(caps)

if __name__ == "__main__":
    unittest.main()
