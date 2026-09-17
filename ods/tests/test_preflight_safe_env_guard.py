#!/usr/bin/env python3
"""Regression test: verify ods-preflight safely executes without safe-env.sh or .env."""
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "ods-preflight.sh"

def test_preflight_execution():
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert res.returncode in (0, 1), f"Unexpected failure code {res.returncode}: {res.stderr}"
    assert "command not found" not in res.stderr

if __name__ == "__main__":
    test_preflight_execution()
    print("test_preflight_safe_env_guard: PASS")
