"""Verify _bad activity snapshot factory validates reason parameter."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.activity import _bad, ActivitySnapshot

class ActivityBadReasonTests(unittest.TestCase):
    def test_empty_reason_rejected(self):
        for bad in ("", None, 123):
            with self.assertRaises(ValueError):
                _bad(bad)

    def test_valid_reason_accepted(self):
        snap = _bad("invalid-clock")
        self.assertIsInstance(snap, ActivitySnapshot)
        self.assertEqual(snap.reason, "invalid-clock")

if __name__ == "__main__":
    unittest.main()
