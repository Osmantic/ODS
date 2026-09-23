"""provider_binding must reject invalid activationId values with ProtocolError."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_access_protocol import provider_binding, ProtocolError


class ProviderBindingUuidTests(unittest.TestCase):
    def test_invalid_uuid_rejected(self):
        bad = {
            "schemaVersion": 1,
            "activationId": "not-a-valid-uuid",
            "revision": 0,
            "allowCloud": False
        }
        with self.assertRaises(ProtocolError):
            provider_binding(bad)

    def test_valid_uuid_accepted(self):
        valid = {
            "schemaVersion": 1,
            "activationId": "12345678-1234-5678-1234-567812345678",
            "revision": 0,
            "allowCloud": True
        }
        self.assertIsNone(provider_binding(valid))


if __name__ == "__main__":
    unittest.main()
