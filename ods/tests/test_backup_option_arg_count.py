#!/usr/bin/env python3
"""Regression test: verify ods-backup.sh validates option argument counts."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


class BackupOptionArgCountTests(unittest.TestCase):
    def test_missing_output_value_rejected(self):
        res = subprocess.run(["bash", str(SCRIPT), "-o"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f"Expected returncode 1, got {res.returncode}")
        combined = res.stdout + res.stderr
        self.assertIn("requires an argument", combined)

    def test_missing_type_value_rejected(self):
        res = subprocess.run(["bash", str(SCRIPT), "-t"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f"Expected returncode 1, got {res.returncode}")
        combined = res.stdout + res.stderr
        self.assertIn("requires an argument", combined)

    def test_help_option_displays_usage(self):
        res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Usage:", res.stdout)


if __name__ == "__main__":
    unittest.main()
