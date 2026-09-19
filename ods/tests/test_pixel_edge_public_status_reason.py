"""Verify public_status rejects trailing hyphens in reason strings."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from access_mode import public_status

class PublicStatusReasonPatternTests(unittest.TestCase):
    def test_trailing_hyphen_reason_rejected(self):
        val = {
            "available": True, "surface": "linux", "configured_mode": "sandboxed",
            "effective_mode": "sandboxed", "runtime_verified": True, "revision": "a" * 64,
            "busy": False, "pending": False, "reason": "transition-failed-", "scope": "owner-host"
        }
        with self.assertRaises(ValueError):
            public_status(val)

    def test_valid_reason_accepted(self):
        val = {
            "available": True, "surface": "linux", "configured_mode": "sandboxed",
            "effective_mode": "sandboxed", "runtime_verified": True, "revision": "a" * 64,
            "busy": False, "pending": False, "reason": "transition-failed", "scope": "owner-host"
        }
        res = public_status(val)
        self.assertEqual(res["reason"], "transition-failed")

if __name__ == "__main__":
    unittest.main()
