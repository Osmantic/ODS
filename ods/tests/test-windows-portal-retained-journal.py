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
ROOT_MATCH = re.search(
    r"function Test-ODSPortalWslInstallRootAbsent\b.*?\$reader = @'\r?\n(.*?)\r?\n'@",
    SOURCE.read_text(encoding="utf-8"), re.S,
)
assert ROOT_MATCH, "stopped-runtime WSL root probe must remain available for contract tests"
ROOT_READER = ROOT_MATCH.group(1).replace("\r\n", "\n")


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

    def run_reader(self, timeout=10):
        return subprocess.run(
            [sys.executable, "-c", READER, str(self.install)],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_missing_journal_is_fresh_install(self):
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "missing"})

    def test_fresh_install_with_no_data_directory_is_missing(self):
        (self.install / "data").rmdir()
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

    def test_fifo_journal_refuses_without_blocking(self):
        os.mkfifo(self.path, 0o600)
        result = self.run_reader(timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.path.is_fifo())

    def test_nonroot_installation_accepts_retained_group_writable_data_directory(self):
        self.write(journal())
        self.assertNotEqual(os.geteuid(), 0, "the fixture must exercise a normal WSL owner")
        self.assertEqual(self.run_reader().returncode, 0)
        data = self.install / "data"
        data.chmod(0o775)
        self.assertEqual(self.run_reader().returncode, 0)
        data.chmod(0o755)
        self.assertEqual(self.run_reader().returncode, 0)


@unittest.skipUnless(hasattr(os, "geteuid"), "WSL root probe requires POSIX ownership")
class FreshInstallRootProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.install = self.parent / "ods"

    def probe(self, path=None):
        return subprocess.run(
            [sys.executable, "-c", ROOT_READER, str(path or self.install)],
            capture_output=True, text=True, timeout=10,
        )

    def test_only_absent_leaf_under_real_parent_is_fresh(self):
        result = self.probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "absent"})
        self.install.mkdir()
        result = self.probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "present"})

    def test_partial_and_symlinked_paths_are_ambiguous(self):
        paths = [self.parent / "missing" / "ods"]
        other = self.parent / "other"
        other.mkdir()
        self.install.symlink_to(other, target_is_directory=True)
        paths.append(self.install)
        linked_parent = self.parent / "linked"
        linked_parent.symlink_to(other, target_is_directory=True)
        paths.append(linked_parent / "ods")
        for path in paths:
            with self.subTest(path=path):
                result = self.probe(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"status": "ambiguous"})


if __name__ == "__main__":
    unittest.main()
