#!/usr/bin/env python3
"""Regression test: verify validate-golden-paths.py handles unreadable or directory paths cleanly."""
import sys
import subprocess
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "validate-golden-paths.py"

def test_unreadable_golden_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = subprocess.run(
            [sys.executable, str(script), tmpdir],
            capture_output=True, text=True
        )
        assert res.returncode == 1, f"Expected 1, got {res.returncode}"
        assert "[FAIL] cannot read golden path file" in res.stdout
        assert "Traceback" not in res.stderr

if __name__ == "__main__":
    test_unreadable_golden_path()
    print("test_validate_golden_paths_os_error: PASS")
