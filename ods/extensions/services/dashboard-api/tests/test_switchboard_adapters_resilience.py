"""Unit test suite for model switchboard adapter contract resilience."""

import sys
import unittest
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard.adapters import result  # type: ignore


class TestSwitchboardAdaptersResilience(unittest.TestCase):
    def test_result_success(self):
        res = result(True, "OK", extra_field="value")
        self.assertTrue(res["ok"])
        self.assertEqual(res["detail"], "OK")
        self.assertEqual(res["extra_field"], "value")

    def test_result_failure(self):
        res = result(False, "Failed")
        self.assertFalse(res["ok"])
        self.assertEqual(res["detail"], "Failed")


if __name__ == "__main__":
    unittest.main()
