"""Verify roles_for validates count range and strict integer type."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import roles_for

class RolesForCountTests(unittest.TestCase):
    def test_invalid_counts_raise_value_error(self):
        for bad in (0, 7, -1, 10, "3", True, False, 2.5, None):
            with self.assertRaises(ValueError):
                roles_for(bad)

    def test_valid_counts_succeed(self):
        for c in range(1, 7):
            roles = roles_for(c)
            self.assertEqual(len(roles), c)

if __name__ == "__main__":
    unittest.main()
