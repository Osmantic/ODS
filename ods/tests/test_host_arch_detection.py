#!/usr/bin/env python3
"""Regression test for installers/lib/host-arch.sh architecture normalization and CLI usage."""

import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "installers" / "lib" / "host-arch.sh"


def run_host_arch(*args: str) -> str:
    res = subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def test_detect_host_arch_amd64_variants():
    assert run_host_arch("x86_64") == "amd64"
    assert run_host_arch("amd64") == "amd64"
    assert run_host_arch("AMD64") == "amd64"
    assert run_host_arch("X86_64") == "amd64"
    assert run_host_arch("x86-64") == "amd64"
    assert run_host_arch("  x86_64  ") == "amd64"


def test_detect_host_arch_arm64_variants():
    assert run_host_arch("aarch64") == "arm64"
    assert run_host_arch("arm64") == "arm64"
    assert run_host_arch("ARM64") == "arm64"
    assert run_host_arch("AARCH64") == "arm64"
    assert run_host_arch("arm64e") == "arm64"
    assert run_host_arch("armv8l") == "arm64"
    assert run_host_arch("  arm64  ") == "arm64"


def test_detect_host_arch_unknown():
    assert run_host_arch("riscv64") == "unknown"
    assert run_host_arch("mips64") == "unknown"
    assert run_host_arch("s390x") == "unknown"


def test_detect_host_arch_standalone_execution():
    # Without arguments, should detect the current machine arch
    out = run_host_arch()
    assert out in ("amd64", "arm64", "unknown")
