import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConversationContractTests(unittest.TestCase):
    def test_reply_drafts_require_the_original_thread(self):
        agents = (ROOT / "workspace-template/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Before drafting a reply, read the original message", agents)
        self.assertIn("Never draft from a subject line", agents)

    def test_recent_owner_actions_outrank_older_projection_absence(self):
        agents = (ROOT / "workspace-template/AGENTS.md").read_text(encoding="utf-8")
        tools = (ROOT / "workspace-template/TOOLS.md").read_text(encoding="utf-8")
        self.assertIn("projection completed before that action cannot disprove it", agents)
        self.assertIn("snapshot cannot contradict them", tools)
        self.assertIn("do not repeat the same lookup until a newer projection exists", agents)

    def test_internal_mechanics_and_false_monitoring_promises_are_forbidden(self):
        agents = (ROOT / "workspace-template/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Do not narrate tool selection", agents)
        self.assertIn("unless an actual scheduled monitor exists", agents)

    def test_workspace_migration_carries_the_same_quality_contract(self):
        migration = (ROOT / "scripts/migrate-workspace-source-boundary.mjs").read_text(encoding="utf-8")
        for phrase in (
            "Before drafting a reply, read the original message",
            "projection completed before that action cannot disprove it",
            "Do not narrate tool selection",
            "unless an actual scheduled monitor exists",
        ):
            self.assertIn(phrase, migration)


if __name__ == "__main__":
    unittest.main()
