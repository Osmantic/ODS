"""Verify project_receipts_valid returns False for non-dict task inputs."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import project_receipts_valid

class ProjectReceiptsTypeTests(unittest.TestCase):
    def test_non_dict_task_returns_false(self):
        for bad in (None, "task", 123, [], True):
            self.assertFalse(project_receipts_valid(bad))

    def test_empty_projects_returns_true(self):
        self.assertTrue(project_receipts_valid({"projects": []}))

if __name__ == "__main__":
    unittest.main()
