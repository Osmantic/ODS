import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/classify-hardware.sh"

def test_classify_hardware_exists():
    assert SCRIPT.is_file()

def test_classify_hardware_syntax():
    res = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0
