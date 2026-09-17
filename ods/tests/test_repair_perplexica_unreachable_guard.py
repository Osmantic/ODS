#!/usr/bin/env python3
"""Regression test: verify repair-perplexica.sh exits 1 with diagnostic on unreachable service."""
import os
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "repair" / "repair-perplexica.sh"

def test_unreachable_perplexica():
    env = {**os.environ, "PERPLEXICA_WAIT_RETRIES": "1", "PERPLEXICA_WAIT_SLEEP": "0"}
    res = subprocess.run(
        ["bash", "-c", f"curl() {{ return 1; }}; export -f curl; bash '{script}' http://127.0.0.1:59999"],
        env=env,
        capture_output=True, text=True
    )
    assert res.returncode == 1, f"Expected 1, got {res.returncode}"
    assert "error: Perplexica unreachable" in res.stderr

if __name__ == "__main__":
    test_unreachable_perplexica()
    print("test_repair_perplexica_unreachable_guard: PASS")
