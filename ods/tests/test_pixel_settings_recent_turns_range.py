"""pixel_settings contract must enforce [0, 12] range on compactionRecentTurns."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError


class SettingsRecentTurnsTests(unittest.TestCase):
    def test_out_of_range_recent_turns_rejected(self):
        for bad in (-1, 13, 100):
            with self.assertRaises(SettingsError):
                validate_preferences({"compactionRecentTurns": bad})

    def test_valid_recent_turns_accepted(self):
        self.assertEqual(validate_preferences({"compactionRecentTurns": 5})["compactionRecentTurns"], 5)


if __name__ == "__main__":
    unittest.main()
