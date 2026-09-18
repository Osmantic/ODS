"""Verify worker constructor validates team_id is a 32-character hex string."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import worker

class WorkerTeamIdTests(unittest.TestCase):
    def test_invalid_team_id_rejected(self):
        for bad in ("invalid", "123", "a" * 31, "a" * 33, "G" * 32, None, 123):
            with self.assertRaises(ValueError):
                worker(bad, 0, "builder")

    def test_valid_team_id_accepted(self):
        valid_id = "a" * 32
        res = worker(valid_id, 0, "builder")
        self.assertEqual(res["chat_id"], f"team-{valid_id}-0")

if __name__ == "__main__":
    unittest.main()
