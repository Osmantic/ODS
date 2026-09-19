"""Verify unavailable envelope enforces valid reason regex formatting."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError
from pixel_settings.public import unavailable

class SettingsUnavailableReasonTests(unittest.TestCase):
    def test_invalid_reasons_rejected(self):
        for bad in (None, 123, "", "UPPERCASE", "bad_underscores", "a" * 97):
            with self.assertRaises(SettingsError):
                unavailable(bad)

    def test_valid_reasons_accepted(self):
        res = unavailable("settings-store-busy")
        self.assertEqual(res["status"], "unavailable")
        self.assertEqual(res["reason"], "settings-store-busy")

if __name__ == "__main__":
    unittest.main()
