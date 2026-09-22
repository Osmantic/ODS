"""Verify ScopeStore._check_target safely handles unconfigured leader."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.scopes import ScopeStore

class ScopesLeaderNoneGuardTests(unittest.TestCase):
    def test_missing_leader_raises_store_error(self):
        config = {
            "revision": 0,
            "enabled": True,
            "roles": {"leader": None, "handoff": "p1"},
            "providers": [{"id": "p1", "enabled": True}]
        }
        rule = {"providerId": "p1", "providerRevision": 0}
        with self.assertRaises(StoreError) as ctx:
            ScopeStore._check_target(config, rule)
        self.assertEqual(str(ctx.exception), "handoff-recipient-not-configured")

if __name__ == "__main__":
    unittest.main()
