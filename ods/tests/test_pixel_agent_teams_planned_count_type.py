"""Verify planned_count returns None for non-string input types without raising."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import planned_count

class PlannedCountTypeTests(unittest.TestCase):
    def test_non_string_returns_none(self):
        for bad in (None, 123, False, True, [], {}):
            self.assertIsNone(planned_count(bad))

    def test_valid_json_count_returned(self):
        self.assertEqual(planned_count('{"count": 3}'), 3)

if __name__ == "__main__":
    unittest.main()
