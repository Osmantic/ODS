"""Verify baseBudgets validation rejects maxOutputTokens exceeding contextTokens."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_settings.contract import SettingsError
from pixel_settings.projection import _validate_state

class BaseBudgetsOutputLimitTests(unittest.TestCase):
    def test_output_exceeding_context_rejected(self):
        state = {
            "schemaVersion": 1,
            "identity": "0" * 64,
            "fields": {},
            "absentParents": [],
            "baseBudgets": {"contextTokens": 4096, "maxOutputTokens": 8192}
        }
        with self.assertRaises(SettingsError) as ctx:
            _validate_state(state)
        self.assertEqual(str(ctx.exception), "invalid-settings-state")

    def test_valid_budgets_accepted(self):
        state = {
            "schemaVersion": 1,
            "identity": "0" * 64,
            "fields": {},
            "absentParents": [],
            "baseBudgets": {"contextTokens": 8192, "maxOutputTokens": 4096}
        }
        _validate_state(state)

if __name__ == "__main__":
    unittest.main()
