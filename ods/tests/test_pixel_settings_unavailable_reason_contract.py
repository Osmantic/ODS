"""pixel_settings unavailable helper must reject invalid reason values."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import SettingsError
from pixel_settings.public import unavailable


class SettingsUnavailableReasonTests(unittest.TestCase):
    def test_invalid_reasons_rejected(self):
        for bad in (None, 123, "", "UPPERCASE", "reason_with_underscore", "a" * 97):
            with self.assertRaises(SettingsError):
                unavailable(bad)

    def test_valid_reason_accepted(self):
        res = unavailable("service-unavailable")
        self.assertEqual(res["status"], "unavailable")
        self.assertEqual(res["reason"], "service-unavailable")


if __name__ == "__main__":
    unittest.main()
