"""Verify decode_frame rejects CRLF line terminators in protocol frames."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import decode_frame, ProtocolError

class PixelProtocolCrlfTests(unittest.TestCase):
    def test_crlf_rejected(self):
        crlf_frame = '{"operation": "status"}\r\n'
        with self.assertRaises(ProtocolError):
            decode_frame(crlf_frame, 16384)

    def test_lf_accepted(self):
        lf_frame = '{"operation": "status"}\n'
        result = decode_frame(lf_frame, 16384)
        self.assertEqual(result, {"operation": "status"})

if __name__ == "__main__":
    unittest.main()
