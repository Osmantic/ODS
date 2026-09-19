"""Verify _validate_roles rejects handoff role present in backups list."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.config import ConfigError, _validate_roles

class HandoffBackupConflictTests(unittest.TestCase):
    def test_handoff_in_backups_rejected(self):
        roles = {"leader": "p1", "backups": ["p2"], "advisor": None, "handoff": "p2"}
        pmap = {"p1": {"id": "p1"}, "p2": {"id": "p2"}}
        with self.assertRaises(ConfigError) as ctx:
            _validate_roles(roles, pmap)
        self.assertEqual(ctx.exception.code, "role_conflict")

    def test_independent_handoff_accepted(self):
        roles = {"leader": "p1", "backups": ["p2"], "advisor": None, "handoff": "p3"}
        pmap = {"p1": {"id": "p1"}, "p2": {"id": "p2"}, "p3": {"id": "p3"}}
        res = _validate_roles(roles, pmap)
        self.assertEqual(res["handoff"], "p3")

if __name__ == "__main__":
    unittest.main()
