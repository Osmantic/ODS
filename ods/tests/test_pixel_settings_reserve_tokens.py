"""pixel_settings contract must reject boolean and negative compactionReserveTokens."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError


class SettingsReserveTokensTests(unittest.TestCase):
    def test_bool_rejected_for_reserve_tokens(self):
        with self.assertRaises(SettingsError):
            validate_preferences({"compactionReserveTokens": True})

    def test_negative_rejected(self):
        with self.assertRaises(SettingsError):
            validate_preferences({"compactionReserveTokens": -50})

    def test_zero_and_positive_accepted(self):
        self.assertEqual(validate_preferences({"compactionReserveTokens": 0})["compactionReserveTokens"], 0)
        self.assertEqual(validate_preferences({"compactionReserveTokens": 1024})["compactionReserveTokens"], 1024)


if __name__ == "__main__":
    unittest.main()
