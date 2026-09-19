"""Verify public_status rejects configured_mode set to unknown."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from access_mode import public_status

class ConfiguredModeKnownTests(unittest.TestCase):
    def test_unknown_configured_mode_rejected(self):
        doc = {
            "available": True,
            "surface": "linux",
            "configured_mode": "unknown",
            "effective_mode": "sandboxed",
            "runtime_verified": True,
            "revision": "0" * 64,
            "busy": False,
            "pending": False,
            "reason": None,
            "scope": "owner-host"
        }
        with self.assertRaises(ValueError):
            public_status(doc)

    def test_valid_modes_accepted(self):
        doc = {
            "available": True,
            "surface": "linux",
            "configured_mode": "sandboxed",
            "effective_mode": "sandboxed",
            "runtime_verified": True,
            "revision": "0" * 64,
            "busy": False,
            "pending": False,
            "reason": None,
            "scope": "owner-host"
        }
        res = public_status(doc)
        self.assertEqual(res["configured_mode"], "sandboxed")

if __name__ == "__main__":
    unittest.main()
