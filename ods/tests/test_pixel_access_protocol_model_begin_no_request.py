"""Verify control_request rejects request payload on model-begin operation."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import ProtocolError, control_request

class ModelBeginNoRequestTests(unittest.TestCase):
    def test_model_begin_with_request_rejected(self):
        val = {"operation": "model-begin", "request": {}}
        with self.assertRaises(ProtocolError):
            control_request(val)

    def test_model_begin_clean_accepted(self):
        val = {"operation": "model-begin"}
        res = control_request(val)
        self.assertEqual(res["operation"], "model-begin")

if __name__ == "__main__":
    unittest.main()
