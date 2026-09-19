"""Verify SettingsError constructor validates message is a non-empty string."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError

class SettingsErrorMessageTests(unittest.TestCase):
    def test_empty_message_rejected(self):
        for bad in ("", "   ", None, 123):
            with self.assertRaises(ValueError):
                SettingsError(bad)

    def test_valid_message_accepted(self):
        err = SettingsError("invalid-settings-fields")
        self.assertEqual(str(err), "invalid-settings-fields")

if __name__ == "__main__":
    unittest.main()
