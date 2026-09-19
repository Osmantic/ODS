"""Verify decode_frame rejects non-positive or non-integer maximum bounds."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import ProtocolError, decode_frame

class DecodeFrameMaxBoundsTests(unittest.TestCase):
    def test_invalid_maximum_rejected(self):
        for bad_max in (0, -1, None, "100", True):
            with self.assertRaises(ProtocolError):
                decode_frame("{}\n", bad_max)

    def test_valid_maximum_accepted(self):
        res = decode_frame("{\"a\": 1}\n", 100)
        self.assertEqual(res, {"a": 1})

if __name__ == "__main__":
    unittest.main()
