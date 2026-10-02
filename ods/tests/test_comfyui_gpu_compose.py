"""Check the effective ComfyUI NVIDIA reservation used by Library add-back."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ODS_ROOT = Path(__file__).resolve().parents[1]
COMFYUI = ODS_ROOT / "extensions" / "services" / "comfyui"


@pytest.mark.parametrize("gpu_uuid,expected_device", [
    ("", "0"),
    ("GPU-selected-by-installer", "GPU-selected-by-installer"),
])
def test_comfyui_uses_one_nvidia_device_reservation(gpu_uuid, expected_device):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable")
    version = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False)
    if version.returncode:
        pytest.skip("Docker Compose plugin is unavailable")

    result = subprocess.run(
        ["docker", "compose",
         "-f", str(COMFYUI / "compose.nvidia.yaml"),
         "-f", str(COMFYUI / "compose.multigpu-nvidia.yaml"),
         "config", "--format", "json"],
        env={**os.environ, "COMFYUI_GPU_UUID": gpu_uuid},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)["services"]["comfyui"]
    reservations = service["deploy"]["resources"]["reservations"]["devices"]
    assert reservations == [{
        "capabilities": ["gpu"],
        "driver": "nvidia",
        "device_ids": [expected_device],
    }]
