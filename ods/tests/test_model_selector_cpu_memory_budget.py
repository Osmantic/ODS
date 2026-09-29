#!/usr/bin/env python3
"""Exercise the production CPU model selector at Tier 0 memory limits."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-model.py"
CATALOG = ROOT / "config" / "model-library.json"


def run_selector(ram_gb: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SELECTOR),
            "--catalog",
            str(CATALOG),
            "--backend",
            "cpu",
            "--memory-type",
            "none",
            "--vram-mb",
            "0",
            "--ram-gb",
            str(ram_gb),
            "--profile",
            "qwen",
            "--tier",
            "0",
            "--max-size-mb",
            "0",
            "--host-arch",
            "amd64",
            "--installable-only",
            "--env",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_four_gb_cpu_host_does_not_get_a_three_gb_model_recommendation():
    result = run_selector(4)
    assert result.returncode == 2, result.stdout
    assert "no installable model fits" in result.stderr


def test_eight_gb_cpu_host_keeps_supported_model_recommendation():
    result = run_selector(8)
    assert result.returncode == 0, result.stderr
    assert 'LLM_MODEL="qwen3.5-2b"' in result.stdout
    assert 'MAX_CONTEXT="65536"' in result.stdout


if __name__ == "__main__":
    test_four_gb_cpu_host_does_not_get_a_three_gb_model_recommendation()
    test_eight_gb_cpu_host_keeps_supported_model_recommendation()
    print("CPU model memory budget tests passed: 2")
