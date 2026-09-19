#!/usr/bin/env python3
"""Regression test: verify build-capability-profile.sh converts float ram_gb to integer MB."""
import subprocess
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "build-capability-profile.sh"

def test_capability_profile_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "caps.json"
        res = subprocess.run(
            ["bash", str(script), "--output", str(out)],
            capture_output=True, text=True
        )
        assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
        assert out.exists()

if __name__ == "__main__":
    test_capability_profile_execution()
    print("test_build_capability_profile_ram_int: PASS")
