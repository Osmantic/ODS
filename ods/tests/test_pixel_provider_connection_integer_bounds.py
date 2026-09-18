"""Verify _integer helper enforces valid low <= high bound invariant."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.connection import _integer

class ConnectionIntegerBoundsTests(unittest.TestCase):
    def test_inverted_bounds_raise_value_error(self):
        with self.assertRaises(ValueError):
            _integer(5, 10, 1)

    def test_valid_bounds_checked(self):
        self.assertTrue(_integer(5, 1, 10))
        self.assertFalse(_integer(0, 1, 10))

if __name__ == "__main__":
    unittest.main()
