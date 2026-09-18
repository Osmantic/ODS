"""Verify questions_valid rejects options that collide after stripping whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import questions_valid

class QuestionOptionsTests(unittest.TestCase):
    def test_duplicate_options_with_whitespace_rejected(self):
        q = [{
            "id": "confirm_step",
            "question": "Proceed with deployment?",
            "options": ["yes", "yes "]
        }]
        self.assertFalse(questions_valid(q))

    def test_distinct_options_accepted(self):
        q = [{
            "id": "confirm_step",
            "question": "Proceed with deployment?",
            "options": ["yes", "no"]
        }]
        self.assertTrue(questions_valid(q))

if __name__ == "__main__":
    unittest.main()
