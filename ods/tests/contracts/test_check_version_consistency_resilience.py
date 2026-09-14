"""Unit test suite for check-version-consistency script resilience."""

import unittest
from pathlib import Path


class TestCheckVersionConsistencyResilience(unittest.TestCase):
    def test_script_existence(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "check-version-consistency.py"
        self.assertTrue(script.exists())


if __name__ == "__main__":
    unittest.main()
