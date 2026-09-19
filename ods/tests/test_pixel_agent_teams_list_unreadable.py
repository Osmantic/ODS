#!/usr/bin/env python3
"""Regression test: TeamStore.list tolerates unreadable, corrupt, or None rows.

The baseline executed row = self.get(owner, ...) followed immediately by
row["chat_id"]. If a file was unreadable, deleted, invalid JSON, or returned
None from get(), list() threw unhandled TypeError or KeyError, crashing team retrieval.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "extensions/services/dashboard-api"))

from pixel_agent_teams import TeamStore


class TeamStoreListUnreadableTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.temp_dir.name).resolve()
        # Ensure private directory permissions for TeamStore
        if os.name == "posix":
            os.chmod(self.store_dir, 0o700)
        self.store = TeamStore(self.store_dir)
        self.owner = "a" * 64
        self.chat = "test-chat-123"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_tolerates_corrupt_and_non_dict_files(self):
        # Write one valid team file
        valid_id = "1" * 32
        valid_data = {
            "id": valid_id,
            "chat_id": self.chat,
            "created": 1000.0,
            "status": "completed",
        }
        valid_path = self.store_dir / f"{self.owner}-{valid_id}.json"
        valid_path.write_text(json.dumps(valid_data), encoding="utf-8")
        if os.name == "posix":
            os.chmod(valid_path, 0o600)

        # Write an invalid JSON file
        corrupt_id = "2" * 32
        corrupt_path = self.store_dir / f"{self.owner}-{corrupt_id}.json"
        corrupt_path.write_text("{corrupt json ...", encoding="utf-8")
        if os.name == "posix":
            os.chmod(corrupt_path, 0o600)

        # Write a non-dict JSON file
        list_id = "3" * 32
        list_path = self.store_dir / f"{self.owner}-{list_id}.json"
        list_path.write_text("[1, 2, 3]", encoding="utf-8")
        if os.name == "posix":
            os.chmod(list_path, 0o600)

        # Listing should not raise; it must return the single valid team
        results = self.store.list(self.owner, self.chat)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], valid_id)


if __name__ == "__main__":
    unittest.main()
