import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ods-support-bundle.sh"

def test_support_bundle_exists():
    assert SCRIPT.is_file()

def test_support_bundle_syntax():
    res = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0
