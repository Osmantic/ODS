"""control_request must require the request field to be a dictionary."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_access_protocol import control_request, ProtocolError


class ProtocolRequestPayloadTests(unittest.TestCase):
    def test_non_dict_request_payload_rejected(self):
        for bad in ("string", 123, None, [1, 2]):
            with self.assertRaises(ProtocolError):
                control_request({"operation": "change", "request": bad})

    def test_valid_request_payload_accepted(self):
        res = control_request({"operation": "change", "request": {"mode": "full-access"}})
        self.assertEqual(res["operation"], "change")


if __name__ == "__main__":
    unittest.main()
