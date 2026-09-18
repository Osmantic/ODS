"""Verify checksum helper strictly checks 64-character length."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import checksum

class ModelChecksumLengthTests(unittest.TestCase):
    def test_invalid_length_checksums_rejected(self):
        for bad in ("a" * 63, "a" * 65, "", 123, None, "A" * 64):
            self.assertFalse(checksum(bad))

    def test_valid_checksum_accepted(self):
        self.assertTrue(checksum("a" * 64))

if __name__ == "__main__":
    unittest.main()
