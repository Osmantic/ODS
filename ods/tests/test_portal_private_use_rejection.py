"""Portal display name contract must reject Unicode private-use codepoints."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import portal_identity_contract

class PortalPrivateUseTests(unittest.TestCase):
    def test_private_use_codepoints_rejected(self):
        with self.assertRaises(ValueError):
            portal_identity_contract.normalize_name("Portal\uE000")

if __name__ == "__main__":
    unittest.main()
