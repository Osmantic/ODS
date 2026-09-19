"""Verify request rejects openclaw path exceeding POSIX PATH_MAX."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import request, ProtocolError

class PixelProtocolOpenclawPathTests(unittest.TestCase):
    def setUp(self):
        self.base_req = {
            "operation": "status",
            "openclaw": "/var/run/openclaw.sock",
            "config_sha256": None,
            "confirmed": True,
        }

    def test_valid_path(self):
        req = request(self.base_req)
        self.assertEqual(req["openclaw"], "/var/run/openclaw.sock")

    def test_oversized_path_rejected(self):
        bad_path = "/" + "a" * 4097
        bad = dict(self.base_req, openclaw=bad_path)
        with self.assertRaises(ProtocolError):
            request(bad)

if __name__ == "__main__":
    unittest.main()
