"""decode_frame must reject oversized strings before performing utf-8 encoding."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_access_protocol import decode_frame, ProtocolError


class ProtocolFrameSizeTests(unittest.TestCase):
    def test_oversized_raw_frame_rejected(self):
        huge = ("x" * 20000) + "\n"
        with self.assertRaises(ProtocolError):
            decode_frame(huge, 1000)

    def test_valid_frame_accepted(self):
        valid = '{"operation": "status"}\n'
        res = decode_frame(valid, 1000)
        self.assertEqual(res["operation"], "status")


if __name__ == "__main__":
    unittest.main()
