#!/usr/bin/env python3
"""Regression test: verify patch-hermes-config.py rejects directory arguments."""
import subprocess
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "patch-hermes-config.py"

def test_directory_path_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = subprocess.run(
            [sys.executable, str(script), tmpdir],
            capture_output=True, text=True
        )
        assert res.returncode == 1, f"Expected 1, got {res.returncode}"
        assert "error: config path is not a file" in res.stderr
        assert "Traceback" not in res.stderr

if __name__ == "__main__":
    test_directory_path_rejected()
    print("test_patch_hermes_config_file_guard: PASS")
