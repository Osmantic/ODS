#!/usr/bin/env python3
"""Regression tests for prune-hermes-slash-workers.sh option argument validation."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "prune-hermes-slash-workers.sh"


class TestPruneHermesSlashWorkersOptions(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Missing value on options requiring arguments exits 1 with diagnostic."""
        for flag in ["--max-count", "--max-age-seconds", "--container"]:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"[FAIL] {flag} requires an argument", proc.stderr)
            self.assertIn("Usage: prune-hermes-slash-workers.sh", proc.stderr)

    def test_empty_option_arguments_rejected(self):
        """Empty string for required options exits 1 with diagnostic."""
        for flag in ["--max-count", "--max-age-seconds", "--container"]:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag, ""],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"[FAIL] {flag} requires an argument", proc.stderr)

    def test_flag_hijacking_rejected(self):
        """Supplying another flag where a value is expected is rejected."""
        cases = [
            (["--container", "--force"], "--container"),
            (["--max-count", "--dry-run"], "--max-count"),
            (["--max-age-seconds", "--container"], "--max-age-seconds"),
        ]
        for args, flag in cases:
            proc = subprocess.run(
                ["bash", str(SCRIPT)] + args,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"[FAIL] {flag} requires an argument", proc.stderr)

    def test_valid_options_dispatch(self):
        """Valid options execute without error in fixture mode."""
        with tempfile.NamedTemporaryFile("w") as fix:
            fix.write("101\t7200\tpython -m hermes.tui_gateway.slash_worker\n")
            fix.flush()
            env = {"ODS_HERMES_SLASH_WORKER_PS_FIXTURE": fix.name}
            proc = subprocess.run(
                ["bash", str(SCRIPT), "--max-count", "5", "--max-age-seconds", "3600", "--container", "ods-hermes"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("[DRY-RUN] rerun with --force to kill selected workers", proc.stdout)


if __name__ == "__main__":
    unittest.main()
