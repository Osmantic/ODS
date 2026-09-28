#!/usr/bin/env python3
"""Regression test: verify snapshot and backup directories have 0700 permissions."""
import os
import stat
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = ROOT / "ods-update.sh"


class TestUpdateSnapshotPermissions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.work_dir = Path(self.temp_dir.name)

        self.install_dir = self.work_dir / "ods"
        self.install_dir.mkdir(parents=True)
        shutil.copy2(UPDATE_SCRIPT, self.install_dir / "ods-update.sh")
        if (ROOT / "lib").exists():
            shutil.copytree(ROOT / "lib", self.install_dir / "lib")

        (self.install_dir / ".env").write_text("ODS_TEST=1\n")
        (self.install_dir / "docker-compose.yml").write_text("version: '3'\n")

        self.backup_dir = self.work_dir / "backups"
        self.backup_dir.mkdir()

    def test_cmd_backup_restricts_directory_permissions(self):
        env = os.environ.copy()
        env["HOME"] = str(self.work_dir)

        res = subprocess.run(
            ["bash", str(self.install_dir / "ods-update.sh"), "backup", "permtest"],
            cwd=str(self.install_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"backup failed: {res.stderr}\n{res.stdout}")

        backup_dir = self.work_dir / ".ods/backups"
        created_dirs = list(backup_dir.glob("backup-permtest-*"))
        self.assertEqual(len(created_dirs), 1)
        backup_path = created_dirs[0]
        mode = stat.S_IMODE(os.stat(backup_path).st_mode)
        self.assertEqual(mode, 0o700, f"Expected 0700 permissions, got {oct(mode)}")

    def test_snapshot_pre_update_restricts_directory_permissions(self):
        env = os.environ.copy()
        env["HOME"] = str(self.work_dir)

        res = subprocess.run(
            [
                "bash",
                "-c",
                f'source "{self.install_dir}/ods-update.sh" help >/dev/null 2>&1; snapshot_pre_update "20260101-120000"',
            ],
            cwd=str(self.install_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"snapshot failed: {res.stderr}\n{res.stdout}")

        snap_dir = self.install_dir / "data/backups/pre-update-20260101-120000"
        self.assertTrue(snap_dir.exists())
        mode = stat.S_IMODE(os.stat(snap_dir).st_mode)
        self.assertEqual(mode, 0o700, f"Expected 0700 permissions, got {oct(mode)}")


if __name__ == "__main__":
    unittest.main()
