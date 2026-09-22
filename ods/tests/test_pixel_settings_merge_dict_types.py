"""Verify merge_preferences validates dict input types directly."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError, merge_preferences

class MergePreferencesDictTypesTests(unittest.TestCase):
    def test_non_dict_rejected(self):
        for bad_cur, bad_chg in ((None, {}), ({}, None), ("str", {}), ({}, 123)):
            with self.assertRaises(SettingsError) as ctx:
                merge_preferences(bad_cur, bad_chg)
            self.assertEqual(str(ctx.exception), "invalid-settings-fields")

    def test_valid_dict_merged(self):
        res = merge_preferences({"contextTokens": 4096}, {"thinking": "low"})
        self.assertEqual(res["contextTokens"], 4096)
        self.assertEqual(res["thinking"], "low")

if __name__ == "__main__":
    unittest.main()
