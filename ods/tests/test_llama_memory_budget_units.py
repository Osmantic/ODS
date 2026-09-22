#!/usr/bin/env python3
"""Regression test for llama memory budget unit parsing and normalization."""

import os
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BUDGET_SCRIPT = ROOT_DIR / "installers" / "lib" / "llama-memory-budget.sh"


def run_bash_fn(fn_name: str, *args: str) -> str:
    escaped_args = " ".join(f'"{a}"' for a in args)
    cmd = (
        f'source "{BUDGET_SCRIPT}" && {fn_name} {escaped_args}'
    )
    result = subprocess.run(
        ["bash", "-c", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_effective_container_memory_gb_units():
    # Unit suffixes: GB, GiB, G, lowercase, whitespace
    assert run_bash_fn("ods_effective_container_memory_gb", "64GB", "8GB") == "8"
    assert run_bash_fn("ods_effective_container_memory_gb", "32GiB", "16GiB") == "16"
    assert run_bash_fn("ods_effective_container_memory_gb", "16G", "32G") == "16"
    assert run_bash_fn("ods_effective_container_memory_gb", "  64  ", "  8  ") == "8"
    assert run_bash_fn("ods_effective_container_memory_gb", "32gb", "0") == "32"
    assert run_bash_fn("ods_effective_container_memory_gb", "invalid", "16GB") == "16"
    assert run_bash_fn("ods_effective_container_memory_gb", "0", "0") == "0"


def test_default_nvidia_llama_memory_limit_units_and_bounds():
    # Fallback when 0 or invalid
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "0") == "64G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "invalid") == "64G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "") == "64G"

    # Sub-16 GiB (reserves 3 GiB, min 1G)
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "2") == "1G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "8") == "5G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "8GB") == "5G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "8GiB") == "5G"

    # 16 GiB and above (reserves 4 GiB, max 64G)
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "16") == "12G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "16G") == "12G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "32GB") == "28G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "64GiB") == "60G"
    assert run_bash_fn("ods_default_nvidia_llama_memory_limit", "128G") == "64G"
