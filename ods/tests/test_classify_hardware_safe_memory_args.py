#!/usr/bin/env python3
"""Regression test: verify classify-hardware handles non-numeric memory arguments safely."""
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if not (repo_root / "scripts").exists():
    repo_root = Path.cwd() / "ods" if (Path.cwd() / "ods").exists() else Path.cwd()
script = repo_root / "scripts" / "classify-hardware.sh"

def test_classify_hardware_args():
    res = subprocess.run(
        [str(script), "--device-id", "0x1234", "--gpu-name", "TestGPU", "--vram-mb", "invalid", "--ram-mb", ""],
        capture_output=True, text=True
    )
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
    assert '"tier":' in res.stdout

if __name__ == "__main__":
    test_classify_hardware_args()
    print("test_classify_hardware_safe_memory_args: PASS")
