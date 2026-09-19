#!/usr/bin/env python3
"""Regression test: verify demo-offline.sh cleanly exits on closed stdin under set -e."""
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "demo-offline.sh"

def test_demo_offline_eof():
    res = subprocess.run(
        ["bash", str(script)],
        input="",
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res.returncode == 0, f"Expected 0 on EOF, got {res.returncode}: {res.stderr}"

if __name__ == "__main__":
    test_demo_offline_eof()
    print("test_demo_offline_noninteractive_stdin: PASS")
