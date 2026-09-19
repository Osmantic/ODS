"""Verify worker constructor validates index is an integer in range 0..5."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import worker

class WorkerIndexTests(unittest.TestCase):
    def test_invalid_index_rejected(self):
        valid_id = "a" * 32
        for bad in (-1, 6, 10, "0", True, False, None, 1.5):
            with self.assertRaises(ValueError):
                worker(valid_id, bad, "builder")

    def test_valid_index_accepted(self):
        valid_id = "a" * 32
        for idx in range(6):
            res = worker(valid_id, idx, "builder")
            self.assertEqual(res["id"], str(idx))

if __name__ == "__main__":
    unittest.main()
