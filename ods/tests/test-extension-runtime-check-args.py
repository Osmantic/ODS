#!/usr/bin/env python3
"""Regression test for scripts/extension-runtime-check.sh argument validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT_DIR / "scripts" / "extension-runtime-check.sh"


class ExtensionRuntimeCheckArgsTests(unittest.TestCase):
    def test_nonexistent_directory_argument_fails_cleanly(self):
        """Non-existent directory path passed as argument must exit 1 with clear error message."""
        result = subprocess.run(["bash", str(CHECK_SCRIPT), "/nonexistent/path/for/ods/root"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Specified directory does not exist", result.stderr)

    def test_default_repo_root_runs_without_crashing(self):
        """Invoking script with repository root must succeed or skip gracefully."""
        result = subprocess.run(["bash", str(CHECK_SCRIPT), str(ROOT_DIR)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Extension runtime check", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
