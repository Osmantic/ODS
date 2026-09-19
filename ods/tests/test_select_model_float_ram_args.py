#!/usr/bin/env python3
"""Regression test: verify select-model.py parses float --ram-gb and --vram-mb arguments."""
import sys
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "select-model.py"
catalog = repo_root / "config" / "model-library.json"

def test_float_ram_arguments():
    res = subprocess.run(
        [sys.executable, str(script), "--catalog", str(catalog), "--backend", "cpu", "--ram-gb", "15.8", "--vram-mb", "0.0", "--env"],
        capture_output=True, text=True
    )
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
    assert "LLM_MODEL=" in res.stdout

if __name__ == "__main__":
    test_float_ram_arguments()
    print("test_select_model_float_ram_args: PASS")
