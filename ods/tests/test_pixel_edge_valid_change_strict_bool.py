"""Verify valid_change returns a strict boolean instead of a Match object."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from access_mode import valid_change

class ValidChangeStrictBoolTests(unittest.TestCase):
    def test_valid_change_returns_strict_bool(self):
        val = {"mode": "sandboxed", "revision": "0" * 64, "confirmed": True}
        res = valid_change(val)
        self.assertIs(type(res), bool)
        self.assertTrue(res)

if __name__ == "__main__":
    unittest.main()
