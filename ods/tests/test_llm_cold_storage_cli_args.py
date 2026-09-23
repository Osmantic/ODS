#!/usr/bin/env python3
"""Regression test for llm-cold-storage.sh CLI argument parsing and error reporting."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
COLD_STORAGE_SH = ROOT_DIR / "scripts" / "llm-cold-storage.sh"


class LLMColdStorageCLIArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env = {
            **os.environ,
            "HF_CACHE": str(Path(self.tmpdir.name) / "hub"),
            "COLD_DIR": str(Path(self.tmpdir.name) / "cold"),
            "LOG_FILE": str(Path(self.tmpdir.name) / "log.txt"),
        }
        Path(self.env["HF_CACHE"]).mkdir(parents=True, exist_ok=True)
        Path(self.env["COLD_DIR"]).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unrecognized_argument_rejected(self):
        """Unrecognized flags and typos must fail with exit code 1 and stderr diagnostic."""
        for flag in ["--unknown-flag", "--excute", "--statuss", "random_arg"]:
            result = subprocess.run(
                ["bash", str(COLD_STORAGE_SH), flag],
                capture_output=True,
                text=True,
                env=self.env,
            )
            self.assertEqual(result.returncode, 1, f"Expected code 1 for {flag}, got {result.returncode}")
            self.assertIn("ERROR: Unrecognized argument", result.stderr)

    def test_help_flag_succeeds(self):
        """Passing --help must exit with 0 and display usage."""
        result = subprocess.run(
            ["bash", str(COLD_STORAGE_SH), "--help"],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)

    def test_restore_missing_argument_fails(self):
        """Passing --restore without a target model must exit with 1."""
        result = subprocess.run(
            ["bash", str(COLD_STORAGE_SH), "--restore"],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage:", result.stdout)

    def test_no_args_dry_run_succeeds(self):
        """Passing no arguments defaults cleanly to dry run scan."""
        result = subprocess.run(
            ["bash", str(COLD_STORAGE_SH)],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry_run=true", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
