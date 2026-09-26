#!/usr/bin/env python3
"""Regression tests for resolve-compose-stack.sh option argument validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "resolve-compose-stack.sh"

FLAGS_WITH_ARGS = [
    "--script-dir",
    "--tier",
    "--gpu-backend",
    "--profile-overlays",
    "--gpu-count",
    "--ods-mode",
    "--skip-gpu-overlays",
    "--skip-gpu-overlays-for",
]


class TestResolveComposeStackOptions(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Missing value on options requiring arguments exits 1 with diagnostic."""
        for flag in FLAGS_WITH_ARGS:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_empty_option_arguments_rejected(self):
        """Empty string for required options exits 1 with diagnostic."""
        for flag in FLAGS_WITH_ARGS:
            proc = subprocess.run(
                ["bash", str(SCRIPT), flag, ""],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_flag_hijacking_rejected(self):
        """Supplying another flag where a value is expected is rejected."""
        cases = [
            (["--tier", "--env"], "--tier"),
            (["--gpu-backend", "--skip-broken"], "--gpu-backend"),
            (["--ods-mode", "--tier"], "--ods-mode"),
        ]
        for args, flag in cases:
            proc = subprocess.run(
                ["bash", str(SCRIPT)] + args,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn(f"Option {flag} requires an argument", proc.stderr)

    def test_unknown_option_rejected(self):
        """Unknown options exit 1 with diagnostic."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--invalid-option"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Unknown argument: --invalid-option", proc.stderr)


if __name__ == "__main__":
    unittest.main()
