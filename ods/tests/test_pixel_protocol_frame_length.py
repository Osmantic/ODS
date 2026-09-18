"""Verify decode_frame guards string length before encoding."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import decode_frame, ProtocolError

class PixelProtocolFrameLengthTests(unittest.TestCase):
    def test_oversized_string_rejected_without_encoding_waste(self):
        oversized = "a" * 20000 + "\n"
        with self.assertRaises(ProtocolError):
            decode_frame(oversized, 16384)

    def test_valid_frame_decoded(self):
        valid = '{"operation": "status"}\n'
        result = decode_frame(valid, 16384)
        self.assertEqual(result, {"operation": "status"})

if __name__ == "__main__":
    unittest.main()
