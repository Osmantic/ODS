"""Exercise admission's real state-file opens in watchdog-isolated processes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import SkipTest

if sys.platform == "win32":
    raise SkipTest("Requires the POSIX transition-state filesystem contract")

import pytest

MODULE = Path(__file__).resolve().parents[1] / "transition_gate.py"
CHILD = r"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

spec = importlib.util.spec_from_file_location("gate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
directory = sys.argv[3]
root = Path(directory)
root.chmod(0o700)
module.initialize(directory)
active = set()
gate = None
target = root / "transition.json"
if sys.argv[2] == "after-startup":
    gate = module.TransitionGate(directory, active)
if sys.argv[2] != "regular":
    target.unlink()
    os.mkfifo(target, 0o600)
if gate is None:
    gate = module.TransitionGate(directory, active)
try:
    status = asyncio.run(gate.status())
    try:
        asyncio.run(gate.admit("fixture-request"))
        admitted = True
    except module.GateError:
        admitted = False
    print(json.dumps({"status": status, "admitted": admitted,
                      "active": len(active), "fifo": stat.S_ISFIFO(target.lstat().st_mode)}))
finally:
    gate.close()
"""


def run_child(phase):
    # The parent owns cleanup even when the child is killed by the watchdog.
    with tempfile.TemporaryDirectory(prefix="ods-gate-state-") as directory:
        return subprocess.run([sys.executable, "-c", CHILD, str(MODULE), phase, directory],
                              capture_output=True, text=True, timeout=3)


@pytest.mark.parametrize("phase", ["startup", "after-startup"])
def test_fifo_state_fails_closed_without_waiting_for_a_writer(phase):
    try:
        result = run_child(phase)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{phase}: transition state read waited for a FIFO writer")
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"]["capability"] == "unavailable"
    assert receipt["status"]["admission_blocked"] is True
    assert receipt["admitted"] is False
    assert receipt["active"] == 0
    assert receipt["fifo"] is True


def test_regular_private_state_still_allows_admission():
    result = run_child("regular")
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"]["capability"] == "available"
    assert receipt["admitted"] is True
    assert receipt["active"] == 1
    assert receipt["fifo"] is False
