"""Verify worker constructor validates role is in ROLES enum."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import worker

class WorkerRoleTests(unittest.TestCase):
    def test_invalid_role_raises_value_error(self):
        valid_id = "a" * 32
        for bad in ("admin", "hacker", "", None, 123):
            with self.assertRaises(ValueError):
                worker(valid_id, 0, bad)

    def test_valid_roles_accepted(self):
        valid_id = "a" * 32
        for r in ("builder", "reviewer", "explorer", "planner", "verifier", "summarizer"):
            res = worker(valid_id, 0, r)
            self.assertEqual(res["role"], r)

if __name__ == "__main__":
    unittest.main()
