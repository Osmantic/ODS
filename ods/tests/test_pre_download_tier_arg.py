#!/usr/bin/env python3
"""Regression test: pre-download.sh rejects missing argument for --tier with clean usage error."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre-download.sh"


class TestPreDownloadTierArg(unittest.TestCase):
    def test_missing_tier_argument_rejected(self):
        res = subprocess.run(
            ["bash", str(SCRIPT), "--tier"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        output = res.stdout + res.stderr
        self.assertIn("Option --tier requires an argument", output)
        self.assertNotIn("unbound variable", output)

    def test_valid_tier_argument_parsed(self):
        # --tier with --help or --list should accept the argument and proceed
        res = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--tier", res.stdout)


if __name__ == "__main__":
    unittest.main()
