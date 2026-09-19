"""Verify _validate_roles rejects handoff role set to same provider as leader."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.config import ConfigError, _validate_roles

class HandoffLeaderConflictTests(unittest.TestCase):
    def test_handoff_identical_to_leader_rejected(self):
        roles = {"leader": "p1", "backups": [], "advisor": None, "handoff": "p1"}
        pmap = {"p1": {"id": "p1"}}
        with self.assertRaises(ConfigError) as ctx:
            _validate_roles(roles, pmap)
        self.assertEqual(ctx.exception.code, "role_conflict")

    def test_distinct_handoff_accepted(self):
        roles = {"leader": "p1", "backups": [], "advisor": None, "handoff": "p2"}
        pmap = {"p1": {"id": "p1"}, "p2": {"id": "p2"}}
        res = _validate_roles(roles, pmap)
        self.assertEqual(res["handoff"], "p2")

if __name__ == "__main__":
    unittest.main()
