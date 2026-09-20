"""Regression test: verify classify-hardware.sh handles non-numeric and negative RAM/VRAM."""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "classify-hardware.sh"


def test_classify_hardware_non_numeric_and_negative_bounds():
    test_cases = [
        ["--vram-mb", "None"],
        ["--ram-mb", "N/A"],
        ["--vram-mb", "null", "--ram-mb", "undefined"],
        ["--vram-mb", "-1024", "--ram-mb", "-2048"],
        ["--vram-mb", "8192.5", "--ram-mb", "16384.0"],
    ]

    for args in test_cases:
        res = subprocess.run(
            ["bash", str(SCRIPT)] + args,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"classify-hardware.sh failed for {args}: {res.stderr}"
        data = json.loads(res.stdout)
        assert "id" in data
        assert "recommended" in data
        assert "backend" in data["recommended"]


if __name__ == "__main__":
    test_classify_hardware_non_numeric_and_negative_bounds()
    print("test_classify_hardware_numeric_bounds: OK")
