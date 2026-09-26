#!/usr/bin/env python3
"""Regression tests for build-capability-profile.sh option argument validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "build-capability-profile.sh"


class TestBuildCapabilityProfileOptions(unittest.TestCase):
    def test_missing_output_argument_rejected(self):
        """Trailing --output option without a value exits 1 with diagnostic."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--output"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR: --output requires an argument", proc.stderr)

    def test_empty_output_argument_rejected(self):
        """Passing an empty string for --output exits 1 with diagnostic."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--output", ""],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR: --output requires an argument", proc.stderr)

    def test_option_hijacking_rejected(self):
        """Supplying another flag where an output path is expected is rejected."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--output", "--env"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR: --output requires an argument", proc.stderr)

    def test_unknown_argument_rejected(self):
        """Unknown arguments exit 1 with diagnostic."""
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--unsupported-option"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Unknown argument: --unsupported-option", proc.stderr)


if __name__ == "__main__":
    unittest.main()
