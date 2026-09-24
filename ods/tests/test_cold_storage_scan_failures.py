"""Uncertain access age must never authorize a model move."""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/llm-cold-storage.sh"


class ScanFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "hub/models--ScanFixture--Model"
        self.model.mkdir(parents=True)
        self.weights = self.model / "weights.bin"
        self.weights.write_bytes(b"retained model")
        old = time.time() - 30 * 86400
        os.utime(self.weights, (old, old))
        self.cold = self.root / "cold"
        self.cold.mkdir()
        self.env = {**os.environ, "HF_CACHE": str(self.model.parent),
                    "COLD_DIR": str(self.cold), "LOG_FILE": str(self.root / "scan.log")}

    def run_script(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, check=False, timeout=15)

    def assert_retained(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.model.is_symlink())
        self.assertTrue(self.weights.is_file())
        self.assertEqual(list(self.cold.iterdir()), [])
        self.assertIn("access scan failed", result.stdout)
        self.assertNotIn("ARCHIVED:", result.stdout)
        self.assertNotIn("WOULD ARCHIVE:", result.stdout)

    def test_partial_native_permission_failure_keeps_model(self):
        if os.getuid() == 0:
            self.skipTest("permission boundary requires an unprivileged user")
        private = self.model / "private"
        private.mkdir()
        (private / "recent.bin").write_bytes(b"recently used")
        private.chmod(0)
        self.addCleanup(private.chmod, 0o700)
        self.assert_retained(self.run_script("--execute"))

    def stat_failure(self, platform, execute):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for command, body in {
            "stat": "echo 'injected stat I/O failure' >&2; exit 1\n",
            "uname": "echo " + platform + "\n",
        }.items():
            path = bin_dir / command
            path.write_text("#!/bin/sh\n" + body)
            path.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.assert_retained(self.run_script(*(["--execute"] if execute else [])))
        self.assertIn("idle unknown", self.run_script("--status").stdout)

    def test_gnu_stat_failure_dry_run(self):
        self.stat_failure("Linux", False)

    def test_gnu_stat_failure_execute(self):
        self.stat_failure("Linux", True)

    def test_bsd_stat_failure_dry_run(self):
        self.stat_failure("Darwin", False)

    def test_bsd_stat_failure_execute(self):
        self.stat_failure("Darwin", True)

    def test_successful_scan_still_archives_and_restores(self):
        result = self.run_script("--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.model.is_symlink())
        self.assertEqual(self.weights.read_bytes(), b"retained model")
        result = self.run_script("--restore", self.model.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.model.is_symlink())
        self.assertEqual(self.weights.read_bytes(), b"retained model")


if __name__ == "__main__":
    unittest.main()
