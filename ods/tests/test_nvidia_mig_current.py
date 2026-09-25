"""Exercise NVIDIA's current MIG CSV contract at the topology boundary."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "installers/lib/nvidia-topo.sh"


@pytest.mark.parametrize(
    ("current", "pending", "expected"),
    [
        ("Enabled", "Disabled", True),
        ("Disabled", "Enabled", False),
        ("Disabled\nEnabled\n[N/A]", "Disabled", True),
        ("[N/A]\nDisabled", "Enabled", False),
        ("  Enabled  \r\n", "Disabled", True),
    ],
)
def test_topology_uses_current_mig_state(tmp_path, current, pending, expected):
    stub = tmp_path / "nvidia-smi"
    stub.write_text(
        """#!/usr/bin/env bash
case "$*" in
  --query-gpu=index,name,*) echo '0, NVIDIA A100, 81920, 81920, 4, 16, GPU-one' ;;
  --query-gpu=mig.mode.current*) printf '%s\\n' "$MIG_CURRENT" ;;
  --query-gpu=driver_version*) echo '580.0' ;;
  'topo -m') printf '        GPU0\\nGPU0    X\\n' ;;
  -q)
    while IFS= read -r current; do
      printf '    MIG Mode\\n        Current : %s\\n        Pending : %s\\n' "$current" "$MIG_PENDING"
    done <<< "$MIG_CURRENT"
    ;;
  *) exit 1 ;;
esac
"""
    )
    stub.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", 'set -euo pipefail; source "$1"; detect_nvidia_topo', "test", str(LIBRARY)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "MIG_CURRENT": current, "MIG_PENDING": pending},
        capture_output=True, text=True, check=True, timeout=10,
    )
    topology = json.loads(result.stdout)
    assert topology["mig_enabled"] is expected
    assert topology["gpus"][0]["uuid"] == "GPU-one"
