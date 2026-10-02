"""Exercise the exact read-only WSL journal projection used by Windows setup."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "installers/windows/lib/wsl-portal-setup.ps1"
MATCH = re.search(r"\$reader = @'\r?\n(.*?)\r?\n'@", SOURCE.read_text(encoding="utf-8"), re.S)
assert MATCH, "retained WSL journal reader must remain available for contract tests"
READER = MATCH.group(1).replace("\r\n", "\n")


def journal(*, phase="completed", outcome="commit", digest="a" * 64):
    return {
        "schemaVersion": 1,
        "transactionId": "b" * 64,
        "phase": phase,
        "outcome": outcome,
        "previous": {"model": "Qwen3.6-35B-A3B-UD-Q4_K_M", "contextLength": 131072},
        "target": {"model": "Qwen3.5-9B-Q4_K_M", "contextLength": 65536},
        "before": {},
        "after": {"windows-runtime-plan": digest},
    }


@unittest.skipUnless(hasattr(os, "geteuid"), "WSL journal projection requires POSIX ownership")
class RetainedJournalProjection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.install = Path(self.temp.name)
        (self.install / "data").mkdir()
        self.path = self.install / "data/pixel-model-transaction.json"

    def write(self, value, mode=0o600):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        self.path.chmod(mode)

    def run_reader(self):
        return subprocess.run(
            [sys.executable, "-c", READER, str(self.install)],
            capture_output=True, text=True, timeout=10,
        )

    def test_missing_journal_is_fresh_install(self):
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "missing"})

    def test_completed_commit_projects_exact_model_context_and_plan_digest(self):
        self.write(journal())
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "status": "completed", "modelId": "Qwen3.5-9B-Q4_K_M",
            "contextLength": 65536, "windowsPlanDigest": "a" * 64,
        })

    def test_completed_rollback_projects_previous_choice(self):
        self.write(journal(outcome="rollback"))
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["modelId"], "Qwen3.6-35B-A3B-UD-Q4_K_M")

    def test_pending_or_missing_plan_digest_cannot_admit_reuse(self):
        for value in (journal(phase="applying"), journal(digest="")):
            with self.subTest(value=value):
                self.write(value)
                self.assertNotEqual(self.run_reader().returncode, 0)

    def test_unsafe_permissions_and_symlink_cannot_admit_reuse(self):
        self.write(journal(), mode=0o644)
        self.assertNotEqual(self.run_reader().returncode, 0)
        self.path.unlink()
        outside = self.install / "outside.json"
        outside.write_text(json.dumps(journal()), encoding="utf-8")
        outside.chmod(0o600)
        self.path.symlink_to(outside)
        self.assertNotEqual(self.run_reader().returncode, 0)

    def test_installation_owner_and_directory_custody(self):
        self.write(journal())
        self.assertNotEqual(os.geteuid(), 0, "the fixture must exercise a normal WSL owner")
        self.assertEqual(self.run_reader().returncode, 0)
        data = self.install / "data"
        data.chmod(0o777)
        self.assertNotEqual(self.run_reader().returncode, 0)
        data.chmod(0o755)
        self.assertEqual(self.run_reader().returncode, 0)


if __name__ == "__main__":
    unittest.main()
