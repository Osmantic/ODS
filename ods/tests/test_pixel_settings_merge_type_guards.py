"""pixel_settings merge_preferences must reject non-dict arguments with SettingsError."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_settings.contract import merge_preferences, SettingsError


class SettingsMergeGuardsTests(unittest.TestCase):
    def test_non_dict_inputs_rejected(self):
        for bad in (None, "string", [1, 2], 123):
            with self.assertRaises(SettingsError):
                merge_preferences(bad, {})
            with self.assertRaises(SettingsError):
                merge_preferences({}, bad)

    def test_valid_merge(self):
        res = merge_preferences({"temperature": 0.5}, {"topP": 0.9})
        self.assertEqual(res["temperature"], 0.5)
        self.assertEqual(res["topP"], 0.9)


if __name__ == "__main__":
    unittest.main()
