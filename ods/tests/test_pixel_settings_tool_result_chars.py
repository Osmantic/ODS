"""pixel_settings contract must reject non-integer toolResultMaxChars values."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError


class SettingsToolResultCharsTests(unittest.TestCase):
    def test_float_and_zero_rejected(self):
        for bad in (0, -1, 500.5, "1000"):
            with self.assertRaises(SettingsError):
                validate_preferences({"toolResultMaxChars": bad})

    def test_valid_chars_accepted(self):
        res = validate_preferences({"toolResultMaxChars": 50000})
        self.assertEqual(res["toolResultMaxChars"], 50000)


if __name__ == "__main__":
    unittest.main()
