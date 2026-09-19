"""Verify TeamStore _path rejects non-string arguments with ValueError."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_agent_teams import TeamStore

class TeamStorePathTypeTests(unittest.TestCase):
    def test_non_string_arguments_raise_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TeamStore(Path(tmp).resolve())
            for bad_owner, bad_id in ((123, "0"*32), ("0"*64, 456), (None, None)):
                with self.assertRaises(ValueError) as ctx:
                    store._path(bad_owner, bad_id)
                self.assertEqual(str(ctx.exception), "Invalid team identity")

if __name__ == "__main__":
    unittest.main()
