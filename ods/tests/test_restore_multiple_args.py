#!/usr/bin/env python3
"""Regression test: verify ods-restore.sh rejects multiple positional backup arguments."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-restore.sh"


class RestoreMultipleArgsTests(unittest.TestCase):
    def test_multiple_backup_arguments_rejected(self):
        res = subprocess.run(
            ["bash", str(SCRIPT), "backup-target-1", "backup-target-2"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1, f"Expected exit code 1, got {res.returncode}")
        combined = res.stdout + res.stderr
        self.assertIn("Multiple backup IDs specified", combined)

    def test_help_option_displays_usage(self):
        res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Usage:", res.stdout)


if __name__ == "__main__":
    unittest.main()
