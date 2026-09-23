import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "agent_comparison_fixture_tool.py"
BOUNDARY = (
    "Deterministic non-promotional comparison rehearsal only; no live provider, credential, network, "
    "external effect, product proof, acceptance, or promotion authority."
)


class AgentComparisonFixtureToolTests(unittest.TestCase):
    def fixture(self, parent: Path) -> Path:
        scenario = parent / ".pixel-scenario"
        scenario.mkdir()
        shutil.copyfile(TOOL, scenario / "tool.py")
        (scenario / "fixture.json").write_text(json.dumps({
            "schemaVersion": 1,
            "operation": "pixel-agent-comparison-fixture",
            "actions": {
                "safe.once": {"response": {"ok": True}, "once": True},
                "private.route": {
                    "response": {"outline": ["a", "b"]},
                    "rejectInputsContaining": ["secret-needle"],
                    "once": True,
                },
                "fault.sequence": {"responses": [
                    {"response": {"error": "lost"}, "exitCode": 75, "stderr": "lost", "committed": True},
                    {"response": {"ok": "after"}, "committed": True},
                ]},
            },
            "boundary": BOUNDARY,
        }), encoding="utf-8")
        return scenario / "tool.py"

    def call(self, tool: Path, action: str, value=None):
        argv = [sys.executable, str(tool), action]
        if value is not None:
            argv.append(json.dumps(value))
        return subprocess.run(argv, cwd=tool.parent.parent, capture_output=True, text=True, timeout=10)

    def test_receipts_are_content_free_and_once_actions_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = self.fixture(Path(temporary))
            first = self.call(tool, "safe.once", {"value": "private-input"})
            duplicate = self.call(tool, "safe.once", {"value": "private-input"})
            self.assertEqual(first.returncode, 0)
            self.assertEqual(json.loads(first.stdout), {"ok": True})
            self.assertEqual(duplicate.returncode, 78)
            journal = [json.loads(line) for line in (tool.parent.parent / ".pixel-state" / "journal.jsonl").read_text().splitlines()]
            self.assertEqual([entry["status"] for entry in journal], ["ok", "duplicate"])
            self.assertNotIn("private-input", json.dumps(journal))
            self.assertTrue(all(entry["liveProvider"] is False and entry["externalEffects"] is False for entry in journal))

    def test_sensitive_input_is_rejected_without_echo_and_fault_commit_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = self.fixture(Path(temporary))
            rejected = self.call(tool, "private.route", {"client": "secret-needle"})
            fault = self.call(tool, "fault.sequence")
            recovered = self.call(tool, "fault.sequence")
            self.assertEqual(rejected.returncode, 77)
            self.assertNotIn("secret-needle", rejected.stdout + rejected.stderr)
            self.assertEqual(fault.returncode, 75)
            self.assertEqual(recovered.returncode, 0)
            journal = [json.loads(line) for line in (tool.parent.parent / ".pixel-state" / "journal.jsonl").read_text().splitlines()]
            self.assertEqual([entry["status"] for entry in journal], ["rejected", "unknown-outcome", "ok"])
            self.assertEqual([entry["committed"] for entry in journal], [False, True, True])
            self.assertNotIn("secret-needle", json.dumps(journal))


if __name__ == "__main__":
    unittest.main()
