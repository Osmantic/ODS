#!/usr/bin/env python3
"""Regression test for llm-cold-storage.sh access days calculation without external bc."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
COLD_STORAGE_SH = ROOT_DIR / "scripts" / "llm-cold-storage.sh"


class LLMColdStorageArithmeticTests(unittest.TestCase):
    def test_get_last_access_days_on_fresh_file(self):
        """Recently accessed file should report 0 days idle using pure bash arithmetic."""
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "model_dir"
            test_dir.mkdir()
            sample_file = test_dir / "weights.bin"
            sample_file.write_bytes(b"model data")

            script = f"""
            source "{COLD_STORAGE_SH}"
            get_last_access_days "{test_dir}"
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = result.stdout.strip()
            self.assertEqual(output, "0", f"Expected 0 days for fresh file, got: {output!r}")

    def test_get_last_access_days_on_empty_dir(self):
        """Empty directory without files must safely output 9999."""
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty_dir"
            empty_dir.mkdir()

            script = f"""
            source "{COLD_STORAGE_SH}"
            get_last_access_days "{empty_dir}"
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = result.stdout.strip()
            self.assertEqual(output, "9999", f"Expected 9999 for empty dir, got: {output!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
