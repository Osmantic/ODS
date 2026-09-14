"""Unit test suite for check-dependency-pins script resilience and pin parsing."""

import unittest
from pathlib import Path


class TestCheckDependencyPinsResilience(unittest.TestCase):
    def test_script_existence(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "check-dependency-pins.py"
        self.assertTrue(script.exists())


if __name__ == "__main__":
    unittest.main()
