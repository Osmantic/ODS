#!/usr/bin/env python3
"""Regression tests for preflight-engine.sh CLI argument validation."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT_SH = ROOT_DIR / "scripts" / "preflight-engine.sh"


class PreflightEngineOptionArgsTests(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Options requiring an argument must fail with exit code 1 and a diagnostic when missing."""
        flags_to_test = [
            "--report",
            "--tier",
            "--ram-gb",
            "--disk-gb",
            "--gpu-backend",
            "--gpu-vram-mb",
            "--gpu-name",
            "--platform-id",
            "--compose-overlays",
            "--script-dir",
            "--host-arch",
            "--disk-policy",
        ]
        for flag in flags_to_test:
            result = subprocess.run(
                ["bash", str(PREFLIGHT_SH), flag],
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
        """Providing valid options with arguments executes preflight report generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.json"
            result = subprocess.run(
                [
                    "bash",
                    str(PREFLIGHT_SH),
                    "--report",
                    str(report_path),
                    "--tier",
                    "1",
                    "--ram-gb",
                    "16",
                    "--disk-gb",
                    "50",
                    "--env",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
