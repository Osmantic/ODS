"""Verify LeaseClaim.finish validates events parameter is a tuple or list."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.lease_claim import LeaseClaim
from pixel_provider.store import StoreError

class LeaseEventsTypeTests(unittest.TestCase):
    def test_invalid_events_type_rejected(self):
        claim = LeaseClaim("/tmp", "12345678-1234-1234-1234-123456789abc", "sess-1", 0)
        claim._fd = 999  # Mock held lease
        for bad in ("events", 123, None, {}):
            with self.assertRaises(StoreError) as ctx:
                claim.finish("closed", events=bad)
            self.assertEqual(str(ctx.exception), "provider-lease-status-invalid")

if __name__ == "__main__":
    unittest.main()
