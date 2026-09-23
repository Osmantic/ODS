#!/usr/bin/env python3
"""Regression test: verify amd_memory_type handles non-numeric and empty byte counts."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "installers" / "lib" / "amd-topo.sh"


class AmdTopoNonNumericBytesTests(unittest.TestCase):
    def _run_amd_memory_type(self, vram, gtt):
        cmd = f'. "{SCRIPT}" && amd_memory_type "{vram}" "{gtt}"'
        return subprocess.run(["bash", "-euo", "pipefail", "-c", cmd], capture_output=True, text=True)

    def test_non_numeric_vram_and_gtt_fallback(self):
        res = self._run_amd_memory_type("invalid", "N/A")
        self.assertEqual(res.returncode, 0, f"Expected returncode 0, got {res.returncode}: {res.stderr}")
        self.assertEqual(res.stdout.strip(), "discrete")

    def test_empty_strings_fallback(self):
        res = self._run_amd_memory_type("", "")
        self.assertEqual(res.returncode, 0, f"Expected returncode 0, got {res.returncode}: {res.stderr}")
        self.assertEqual(res.stdout.strip(), "discrete")

    def test_valid_discrete_memory(self):
        # 8GB VRAM (8589934592), 4GB GTT (4294967296)
        res = self._run_amd_memory_type("8589934592", "4294967296")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "discrete")

    def test_valid_unified_memory(self):
        # 1GB VRAM (1073741824), 32GB GTT (34359738368)
        res = self._run_amd_memory_type("1073741824", "34359738368")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "unified")


if __name__ == "__main__":
    unittest.main()
