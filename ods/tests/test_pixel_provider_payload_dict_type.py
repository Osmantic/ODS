"""Verify select_candidates rejects non-dict payload and missing messages list."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.runtime_policy import select_candidates

class RuntimePolicyPayloadTypeTests(unittest.TestCase):
    def test_malformed_payload_rejected(self):
        for bad in (None, "payload", 123, [], {}, {"messages": "not-a-list"}, {"messages": None}):
            with self.assertRaises(StoreError) as ctx:
                select_candidates({"enabled": True}, bad)
            self.assertEqual(str(ctx.exception), "invalid-inference-payload")

if __name__ == "__main__":
    unittest.main()
