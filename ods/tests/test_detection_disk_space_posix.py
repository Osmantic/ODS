"""Regression test: verify test_disk_space uses POSIX df -Pk fallback when df -g fails."""

import os
import stat
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "installers" / "macos" / "lib" / "detection.sh"


def test_disk_space_live():
    """Verify live execution of test_disk_space sets integer GB and boolean sufficient."""
    cmd = f". '{SCRIPT}'; test_disk_space; echo \"$DISK_FREE_GB $DISK_SUFFICIENT\""
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False)
    assert res.returncode == 0
    parts = res.stdout.strip().split()
    assert len(parts) == 2
    free_gb, sufficient = parts
    assert free_gb.isdigit()
    assert sufficient in ("true", "false")


def test_disk_space_posix_fallback():
    """Verify fallback to POSIX df -Pk when df -g is unavailable or fails."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        fake_df = tmp_path / "df"
        # Create a stub df that fails on -g but supports -Pk
        fake_df.write_text(
            "#!/bin/sh\n"
            "for arg in \"$@\"; do\n"
            '    if [ "$arg" = "-g" ]; then\n'
            "        echo 'df: illegal option -- g' >&2\n"
            "        exit 64\n"
            "    fi\n"
            "done\n"
            'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
            'echo "/dev/fake 104857600 52428800 52428800 50% /"\n'
        )
        fake_df.chmod(fake_df.stat().st_mode | stat.S_IEXEC)

        env = os.environ.copy()
        env["PATH"] = f"{tmpdir}:{env.get('PATH', '')}"

        cmd = f". '{SCRIPT}'; test_disk_space '/'; echo \"$DISK_FREE_GB $DISK_SUFFICIENT\""
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, check=False)
        assert res.returncode == 0
        parts = res.stdout.strip().split()
        assert len(parts) == 2
        free_gb, sufficient = parts
        # 52428800 KB / 1048576 = 50 GB
        assert free_gb == "50"
        assert sufficient == "true"


if __name__ == "__main__":
    test_disk_space_live()
    test_disk_space_posix_fallback()
    print("test_detection_disk_space_posix: OK")
