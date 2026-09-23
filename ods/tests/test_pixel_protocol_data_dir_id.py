"""control_request must require data_dir_id to be a non-empty string."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_access_protocol import control_request, ProtocolError


class ProtocolDataDirIdTests(unittest.TestCase):
    def test_invalid_data_dir_ids_rejected(self):
        for bad in (None, 12345, "", "   ", ["id"]):
            with self.assertRaises(ProtocolError):
                control_request({"operation": "settings-status", "data_dir_id": bad})

    def test_valid_data_dir_id_accepted(self):
        res = control_request({"operation": "settings-status", "data_dir_id": "a" * 64})
        self.assertEqual(res["operation"], "settings-status")


if __name__ == "__main__":
    unittest.main()
