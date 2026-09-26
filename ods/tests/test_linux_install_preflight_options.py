#!/usr/bin/env python3
"""Regression tests for linux-install-preflight.sh option argument validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "linux-install-preflight.sh"


class TestLinuxInstallPreflightOptions(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Missing value on options requiring arguments exits 2 with diagnostic."""
        for flag in ["--json-file", "--ods-root", "--min-disk-gb"]:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_empty_option_arguments_rejected(self):
        """Empty string for required options exits 2 with diagnostic."""
        for flag in ["--json-file", "--ods-root", "--min-disk-gb"]:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag, ""],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_flag_hijacking_rejected(self):
        """Supplying another flag where a value is expected is rejected."""
        cases = [
            (["--json-file", "--strict"], "--json-file"),
            (["--ods-root", "--json"], "--ods-root"),
            (["--min-disk-gb", "--ods-root"], "--min-disk-gb"),
        ]
        for args, flag in cases:
            proc = subprocess.run(
                ["bash", str(SCRIPT)] + args,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_unknown_option_rejected(self):
        """Unknown options exit 2 with diagnostic."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--invalid-option"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Unknown option: --invalid-option", proc.stderr)


if __name__ == "__main__":
    unittest.main()
