"""Verify checksum accepts uppercase and mixed-case hex SHA256."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import checksum

class PixelModelFingerprintCaseTests(unittest.TestCase):
    def test_uppercase_checksum_accepted(self):
        upper_hash = "A" * 64
        self.assertTrue(checksum(upper_hash))

    def test_lowercase_checksum_accepted(self):
        lower_hash = "a" * 64
        self.assertTrue(checksum(lower_hash))

    def test_invalid_length_rejected(self):
        self.assertFalse(checksum("A" * 63))
        self.assertFalse(checksum("A" * 65))
        self.assertFalse(checksum("Z" * 64))

if __name__ == "__main__":
    unittest.main()
