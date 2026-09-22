"""Regression test: verify ods-backup.sh falls back to SCRIPT_DIR/lib when ODS_DIR lacks lib/."""

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


def test_backup_falls_back_to_script_dir_lib():
    with tempfile.TemporaryDirectory() as tmpdir:
        # tmpdir contains NO lib/ directory
        env = os.environ.copy()
        env["ODS_DIR"] = tmpdir
        res = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert res.returncode == 0, f"Script failed: {res.stderr}"
        assert "No such file or directory" not in res.stderr
        assert "Usage: ods-backup.sh" in res.stdout


if __name__ == "__main__":
    test_backup_falls_back_to_script_dir_lib()
    print("test_backup_script_lib_fallback: OK")
