"""Verify TeamStore list does not crash when get returns None for a file."""
import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import TeamStore

class TeamStoreListNullGuardTests(unittest.TestCase):
    def test_null_row_from_get_safely_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TeamStore(Path(tmp).resolve())
            owner = "a" * 64
            dummy_file = Path(tmp).resolve() / f"{owner}-{'0'*32}.json"
            dummy_file.write_text("{}")
            with patch.object(store, "get", return_value=None):
                rows = store.list(owner, "chat1")
                self.assertEqual(rows, [])

if __name__ == "__main__":
    unittest.main()
