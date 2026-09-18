"""Verify questions_valid rejects questions and options with unstripped whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import questions_valid

class QuestionsWhitespaceTests(unittest.TestCase):
    def test_unstripped_question_rejected(self):
        val = [{
            "id": "q1",
            "question": " What should we do? ",
            "options": ["option 1", "option 2"]
        }]
        self.assertFalse(questions_valid(val))

    def test_unstripped_option_rejected(self):
        val = [{
            "id": "q1",
            "question": "What should we do?",
            "options": [" option 1 ", "option 2"]
        }]
        self.assertFalse(questions_valid(val))

    def test_clean_question_accepted(self):
        val = [{
            "id": "q1",
            "question": "What should we do?",
            "options": ["option 1", "option 2"]
        }]
        self.assertTrue(questions_valid(val))

if __name__ == "__main__":
    unittest.main()
