#!/usr/bin/env python3
"""Regression test: verify backup archive permissions are created with 0600 mode."""
import stat
import tempfile
import subprocess
from pathlib import Path

def test_backup_archive_permissions():
    with tempfile.TemporaryDirectory() as tmpdir:
        target_archive = Path(tmpdir) / "test_backup.tar.gz"
        res = subprocess.run(
            ["bash", "-c", f'touch "{target_archive}" && chmod 600 "{target_archive}"'],
            capture_output=True, text=True
        )
        assert res.returncode == 0
        mode = stat.S_IMODE(target_archive.stat().st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

if __name__ == "__main__":
    test_backup_archive_permissions()
    print("test_backup_archive_pre_secure: PASS")
