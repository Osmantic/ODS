"""Verify _capabilities rejects backendContextTokens below minimum 4096 tokens."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError, _capabilities

class BackendContextTokensFloorTests(unittest.TestCase):
    def test_backend_context_below_4096_rejected(self):
        caps = {
            "providerContextTokens": 8192,
            "providerMaxOutputTokens": 4096,
            "activeContextTokens": 8192,
            "activeMaxOutputTokens": 4096,
            "backendContextTokens": 2048,
            "capacitySource": "backend-observed",
            "supportedThinkingLevels": ["off"],
            "samplingSupported": True,
            "pixelOnlyRuntime": True
        }
        with self.assertRaises(SettingsError):
            _capabilities(caps)

if __name__ == "__main__":
    unittest.main()
