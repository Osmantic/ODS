"""Verify valid_history_snapshot returns False on non-dict inputs."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from chat_context import valid_history_snapshot

class HistorySnapshotDictTypeTests(unittest.TestCase):
    def test_non_dict_returns_false(self):
        for bad in (None, "data", 123, [], True):
            self.assertFalse(valid_history_snapshot(bad))

if __name__ == "__main__":
    unittest.main()
