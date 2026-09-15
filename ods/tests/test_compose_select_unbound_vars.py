#!/usr/bin/env python3
"""Regression test for installers/lib/compose-select.sh under strict bash settings (set -euo pipefail)."""

import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "installers" / "lib" / "compose-select.sh"


def run_bash_cmd(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_compose_select_under_strict_set_u():
    # Calling resolve_compose_config with NO pre-set environment variables under set -euo pipefail
    cmd = f'set -euo pipefail; source "{SCRIPT_PATH}"; resolve_compose_config'
    proc = run_bash_cmd(cmd)
    assert proc.returncode == 0, f"Failed with: {proc.stderr}"


def test_compose_select_missing_logging_stubs():
    # Verify no 'command not found' when log, warn, and load_env_from_output are undefined
    cmd = f'set -euo pipefail; source "{SCRIPT_PATH}"; resolve_compose_config'
    proc = run_bash_cmd(cmd)
    assert "command not found" not in proc.stderr
    assert "unbound variable" not in proc.stderr


def test_compose_select_with_tier_and_backend():
    cmd = (
        f'set -euo pipefail; export TIER=NV_ULTRA GPU_BACKEND=nvidia SCRIPT_DIR="{ROOT_DIR}"; '
        f'source "{SCRIPT_PATH}"; resolve_compose_config; echo "FLAGS=$COMPOSE_FLAGS"'
    )
    proc = run_bash_cmd(cmd)
    assert proc.returncode == 0, f"Failed with: {proc.stderr}"
    assert "-f docker-compose.nvidia.yml" in proc.stdout
