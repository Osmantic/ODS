"""pixel_settings contract must enforce [1, 2_000_000] bounds on bootstrapMaxChars."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError


class SettingsBootstrapCharsTests(unittest.TestCase):
    def test_out_of_bound_bootstrap_chars_rejected(self):
        for bad in (0, -1, 2_000_001):
            with self.assertRaises(SettingsError):
                validate_preferences({"bootstrapMaxChars": bad})

    def test_valid_bootstrap_chars_accepted(self):
        res = validate_preferences({"bootstrapMaxChars": 10000})
        self.assertEqual(res["bootstrapMaxChars"], 10000)


if __name__ == "__main__":
    unittest.main()
