"""pixel_settings contract must reject nan, inf, and bools for number controls."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import validate_preferences, SettingsError


class SettingsNumberValidationTests(unittest.TestCase):
    def test_nan_and_inf_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(SettingsError):
                validate_preferences({"temperature": bad})

    def test_bool_rejected_for_number(self):
        with self.assertRaises(SettingsError):
            validate_preferences({"temperature": True})

    def test_valid_number_accepted(self):
        res = validate_preferences({"temperature": 0.7})
        self.assertEqual(res["temperature"], 0.7)


if __name__ == "__main__":
    unittest.main()
