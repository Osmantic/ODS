#!/usr/bin/env python3
"""Regression test: TeamManager.view tolerates missing or null agent conversation.

The baseline accessed agent['conversation'] directly without checking for existence
or None. If an agent crashed before conversation initialization or stored corrupted
messages, view() raised KeyError or TypeError.
"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "extensions/services/dashboard-api"))

from pixel_agent_teams import TeamStore, TeamManager


class TeamManagerViewConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.temp_dir.name).resolve()
        if os.name == "posix":
            os.chmod(self.store_dir, 0o700)
        self.store = TeamStore(self.store_dir)
        self.manager = TeamManager(self.store, run=None, cancel=None)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_view_tolerates_missing_conversation(self):
        row = {
            "id": "team-123",
            "status": "failed",
            "instance": self.store.instance,
            "agents": [
                {"status": "failed", "role": "explorer"},  # missing conversation
                {"status": "failed", "role": "reviewer", "conversation": None},
                {"status": "failed", "role": "verifier", "conversation": ["non-dict"]},
            ],
        }
        # Calling view must not raise KeyError or TypeError
        viewed = self.manager.view(row)
        self.assertEqual(len(viewed["agents"]), 3)
        for agent in viewed["agents"]:
            self.assertTrue(agent.get("retryable"))
            self.assertIn("No completed response", agent["error"])


if __name__ == "__main__":
    unittest.main()
