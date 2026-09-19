"""Verify pixel_access_protocol validates transaction_id for model operations."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import request, ProtocolError

class PixelProtocolModelTxIdTests(unittest.TestCase):
    def setUp(self):
        self.valid_hex = "a" * 64
        self.base_req = {
            "operation": "model-begin",
            "openclaw": "/var/run/openclaw.sock",
            "config_sha256": "b" * 64,
            "confirmed": True,
            "transaction_id": self.valid_hex,
        }

    def test_valid_transaction_id(self):
        req = request(self.base_req)
        self.assertEqual(req["transaction_id"], self.valid_hex)

    def test_invalid_transaction_id_rejected(self):
        for bad_tx in ("not-hex", "123", "", None, 12345):
            bad = dict(self.base_req, transaction_id=bad_tx)
            with self.assertRaises(ProtocolError):
                request(bad)

if __name__ == "__main__":
    unittest.main()
