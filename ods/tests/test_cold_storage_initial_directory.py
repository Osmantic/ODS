#!/usr/bin/env python3
"""Regression test for llm-cold-storage.sh initial directory and mv failure handling."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
COLD_STORAGE_SH = ROOT_DIR / "scripts" / "llm-cold-storage.sh"


class ColdStorageInitialDirectoryTests(unittest.TestCase):
    def test_archive_creates_missing_cold_directory(self):
        """Archiving idle models must create COLD_DIR, move the directory, and symlink."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cache = root / "hf_cache"
            name = "models--test--idle-model"
            model_dir = cache / name
            model_dir.mkdir(parents=True)
            weights = model_dir / "weights.bin"
            weights.write_bytes(b"model payload")
            os.utime(weights, (1_000_000_000, 1_000_000_000))

            cold_dir = root / "missing_cold_dir"
            log_file = root / "cold.log"
            env = {
                **os.environ,
                "HF_CACHE": str(cache),
                "COLD_DIR": str(cold_dir),
                "LOG_FILE": str(log_file),
            }

            self.assertFalse(cold_dir.exists())

            # 1. Dry run: COLD_DIR must not be created, model must remain untouched
            res_dry = subprocess.run(
                ["bash", str(COLD_STORAGE_SH)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_dry.returncode, 0)
            self.assertIn("WOULD ARCHIVE", res_dry.stdout)
            self.assertFalse(cold_dir.exists())
            self.assertFalse(model_dir.is_symlink())

            # 2. Execute archive: creates missing COLD_DIR, moves model, symlinks original
            res_exec = subprocess.run(
                ["bash", str(COLD_STORAGE_SH), "--execute"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_exec.returncode, 0)
            self.assertTrue(cold_dir.is_dir(), "COLD_DIR must be created")
            self.assertTrue(model_dir.is_symlink(), "model_dir must be a symlink")
            self.assertEqual(model_dir.resolve(), (cold_dir / name).resolve())
            self.assertEqual((model_dir / "weights.bin").read_bytes(), b"model payload")
            self.assertIn("1 archived, 0 skipped", res_exec.stdout)

            # 3. Second run: already archived model must be skipped
            res_second = subprocess.run(
                ["bash", str(COLD_STORAGE_SH), "--execute"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_second.returncode, 0)
            self.assertIn("0 archived, 0 skipped", res_second.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses the read-only directory")
    def test_archive_mv_failure_does_not_symlink_or_report_archived(self):
        """A failed move preserves the model and returns a failed scan."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cache = root / "hf_cache"
            name = "models--test--failing-model"
            model_dir = cache / name
            model_dir.mkdir(parents=True)
            weights = model_dir / "weights.bin"
            weights.write_bytes(b"model payload")
            os.utime(weights, (1_000_000_000, 1_000_000_000))

            # Make cold_dir unwritable so mv fails
            cold_dir = root / "readonly_cold_dir"
            cold_dir.mkdir()
            cold_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x (no write permission)

            log_file = root / "cold_fail.log"
            env = {
                **os.environ,
                "HF_CACHE": str(cache),
                "COLD_DIR": str(cold_dir),
                "LOG_FILE": str(log_file),
            }

            try:
                res = subprocess.run(
                    ["bash", str(COLD_STORAGE_SH), "--execute"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(res.returncode, 0)
                # Model must remain a directory, not a symlink
                self.assertFalse(model_dir.is_symlink(), "model_dir must not be replaced with a symlink on failed mv")
                self.assertTrue(model_dir.is_dir())
                # Must not report ARCHIVED in log
                log_content = log_file.read_text(encoding="utf-8")
                self.assertNotIn(f"ARCHIVED: {name}", log_content)
                self.assertIn("ERROR: failed to move", log_content)
                # An attempted operation failed; this was not an ordinary skip.
                self.assertIn("0 archived, 0 skipped, 1 failed", log_content)
            finally:
                # restore write permission for cleanup
                cold_dir.chmod(stat.S_IRWXU)


if __name__ == "__main__":
    unittest.main(verbosity=2)
