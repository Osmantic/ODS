#!/usr/bin/env python3
"""Regression test: verify get_host_logical_cpus in detection.sh resolves core count."""
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "installers" / "lib" / "detection.sh"

def test_cpu_detection():
    cmd = f". '{script}'; get_host_logical_cpus"
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0
    val = res.stdout.strip()
    assert val.isdigit()
    assert int(val) >= 1

if __name__ == "__main__":
    test_cpu_detection()
    print("test_detection_darwin_cpu_cores: PASS")
