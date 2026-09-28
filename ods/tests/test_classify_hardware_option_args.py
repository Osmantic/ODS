#!/usr/bin/env python3
"""Regression tests for classify-hardware.sh CLI argument validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
CLASSIFY_SH = ROOT_DIR / "scripts" / "classify-hardware.sh"


class ClassifyHardwareOptionArgsTests(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Options requiring an argument must fail with exit code 1 and a diagnostic when missing."""
        flags_to_test = [
            "--device-id",
            "--gpu-name",
            "--platform-id",
            "--gpu-vendor",
            "--memory-type",
            "--vram-mb",
            "--cpu-name",
            "--ram-mb",
            "--db",
        ]
        for flag in flags_to_test:
            result = subprocess.run(
                ["bash", str(CLASSIFY_SH), flag],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                1,
                f"Expected exit code 1 when missing argument for {flag}, got {result.returncode}",
            )
            self.assertIn(
                f"ERROR: {flag} requires an argument",
                result.stderr,
                f"Expected error message on stderr for {flag}, got: {result.stderr!r}",
            )

    def test_valid_options_succeed(self):
        """Providing valid options with arguments executes classification successfully."""
        result = subprocess.run(
            [
                "bash",
                str(CLASSIFY_SH),
                "--platform-id",
                "darwin_apple_silicon",
                "--ram-mb",
                "16384",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id":', result.stdout)

        env_result = subprocess.run(
            [
                "bash",
                str(CLASSIFY_SH),
                "--env",
                "--platform-id",
                "darwin_apple_silicon",
                "--ram-mb",
                "16384",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(env_result.returncode, 0, env_result.stderr)
        self.assertIn("HW_CLASS_ID=", env_result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
