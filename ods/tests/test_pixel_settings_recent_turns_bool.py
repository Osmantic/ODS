"""Verify validate_preferences rejects boolean values for integer controls."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError

class PixelSettingsRecentTurnsBoolTests(unittest.TestCase):
    def test_integer_control_rejects_boolean(self):
        for bad_val in (True, False):
            with self.assertRaises(SettingsError):
                validate_preferences({"compactionRecentTurns": bad_val})

    def test_valid_integer_accepted(self):
        prefs = validate_preferences({"compactionRecentTurns": 4})
        self.assertEqual(prefs["compactionRecentTurns"], 4)

if __name__ == "__main__":
    unittest.main()
