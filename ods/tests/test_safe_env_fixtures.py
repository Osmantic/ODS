import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE_ENV_SH = ROOT / "lib/safe-env.sh"

def test_safe_env_script_exists():
    assert SAFE_ENV_SH.is_file()
    assert (SAFE_ENV_SH.stat().st_mode & 0o111) != 0

def test_safe_env_syntax_check():
    res = subprocess.run(["bash", "-n", str(SAFE_ENV_SH)], capture_output=True, text=True)
    assert res.returncode == 0
