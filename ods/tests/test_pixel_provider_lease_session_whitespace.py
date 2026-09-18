"""Verify LeaseClaim rejects session IDs with unstripped whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.lease_claim import LeaseClaim
from pixel_provider.store import StoreError

class LeaseSessionWhitespaceTests(unittest.TestCase):
    def test_unstripped_session_id_rejected(self):
        for bad in (" sess-1", "sess-1 ", " sess-1 "):
            with self.assertRaises(StoreError) as ctx:
                LeaseClaim("/tmp", "12345678-1234-1234-1234-123456789abc", bad, 0)
            self.assertEqual(str(ctx.exception), "provider-session-invalid")

    def test_clean_session_id_accepted(self):
        claim = LeaseClaim("/tmp", "12345678-1234-1234-1234-123456789abc", "sess-1", 0)
        self.assertEqual(claim.session_id, "sess-1")

if __name__ == "__main__":
    unittest.main()
