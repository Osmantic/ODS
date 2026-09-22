#!/usr/bin/env python3
"""Regression test for robust argument parsing in classify-hardware.sh."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "classify-hardware.sh"


class ClassifyHardwareArgTests(unittest.TestCase):
    def test_non_numeric_vram_and_ram_mb_do_not_crash(self):
        """Non-numeric values must not raise an unhandled ValueError traceback."""
        result = subprocess.run(
            [str(SCRIPT_PATH), "--vram-mb", "invalid", "--ram-mb", "unparsed", "--env"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")
        self.assertIn('HW_CLASS_ID="unknown"', result.stdout)

    def test_unit_suffixed_ram_mb_is_parsed_correctly(self):
        """Values with trailing MB/unit suffixes should parse the leading integer."""
        result = subprocess.run(
            [str(SCRIPT_PATH), "--vram-mb", "8192MB", "--ram-mb", "32768 MB", "--env"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")
        self.assertIn('HW_CLASS_ID=', result.stdout)

    def test_json_output_mode_with_empty_strings(self):
        """JSON output mode should produce valid parseable JSON when args are empty."""
        result = subprocess.run(
            [str(SCRIPT_PATH), "--vram-mb", "", "--ram-mb", ""],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")
        payload = json.loads(result.stdout)
        self.assertIn("id", payload)
        self.assertIn("recommended", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
