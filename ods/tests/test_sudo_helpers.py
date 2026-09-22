#!/usr/bin/env python3
"""Regression test for installers/lib/sudo.sh UI fallbacks and safe execution."""

import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SUDO_SCRIPT = ROOT_DIR / "installers" / "lib" / "sudo.sh"


def run_bash_snippet(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", snippet],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_sudo_helpers_no_ui_stubs():
    # Verify ods_prepare_sudo works without pre-defined ai/ai_warn functions
    cmd = (
        f'set -euo pipefail; export DRY_RUN=true; source "{SUDO_SCRIPT}"; '
        'ods_prepare_sudo "test"; echo "AVAILABLE=$ODS_SUDO_AVAILABLE"'
    )
    proc = run_bash_snippet(cmd)
    assert proc.returncode == 0, f"Failed: {proc.stderr}"
    assert "AVAILABLE=true" in proc.stdout


def test_ods_sudo_empty_args():
    # Calling ods_sudo with no arguments should safely return 0
    cmd = f'set -euo pipefail; source "{SUDO_SCRIPT}"; ods_sudo'
    proc = run_bash_snippet(cmd)
    assert proc.returncode == 0, f"Failed: {proc.stderr}"


def test_ods_sudo_unavailable_skip():
    # When ODS_SUDO_AVAILABLE=false, command should be skipped and return 0
    cmd = (
        f'set -euo pipefail; source "{SUDO_SCRIPT}"; '
        'export ODS_SUDO_AVAILABLE=false; ods_sudo echo "should_not_run"'
    )
    proc = run_bash_snippet(cmd)
    assert proc.returncode == 0, f"Failed: {proc.stderr}"
    assert "should_not_run" not in proc.stdout


def test_ods_sudo_available_predicate():
    cmd = f'source "{SUDO_SCRIPT}"; export ODS_SUDO_AVAILABLE=true; ods_sudo_available'
    proc = run_bash_snippet(cmd)
    assert proc.returncode == 0
