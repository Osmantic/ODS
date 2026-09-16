#!/usr/bin/env python3
"""Regression test: verify load-backend-contract.sh rejects missing --backend argument."""
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "load-backend-contract.sh"

def test_missing_backend_arg():
    res = subprocess.run(["bash", str(script), "--backend"], capture_output=True, text=True)
    assert res.returncode == 1, f"Expected exit 1, got {res.returncode}"
    assert "Missing value for argument: --backend" in res.stderr

if __name__ == "__main__":
    test_missing_backend_arg()
    print("test_load_backend_missing_arg: PASS")
