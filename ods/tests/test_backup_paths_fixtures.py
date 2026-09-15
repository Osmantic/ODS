import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lib/backup-paths.sh"

def test_backup_paths_exists():
    assert SCRIPT.is_file()

def test_backup_paths_syntax():
    res = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0
