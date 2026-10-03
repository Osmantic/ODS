"""Check that the pinned CPU TEI image has no GPU request after Compose merge."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ODS_ROOT = Path(__file__).resolve().parents[1]
EMBEDDINGS = ODS_ROOT / "extensions" / "services" / "embeddings"


@pytest.mark.parametrize("overlay", [
    "compose.multigpu-nvidia.yaml",
    "compose.multigpu-amd.yaml",
])
def test_embeddings_cpu_image_ignores_multi_gpu_assignment(overlay):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable")
    version = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False)
    if version.returncode:
        pytest.skip("Docker Compose plugin is unavailable")

    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(ODS_ROOT),
         "-f", str(EMBEDDINGS / "compose.yaml"),
         "-f", str(EMBEDDINGS / overlay),
         "config", "--format", "json"],
        env={**os.environ, "EMBEDDINGS_GPU_UUID": "", "EMBEDDINGS_GPU_INDEX": "0"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)["services"]["embeddings"]
    assert ":cpu-1.9.1@" in service["image"]
    assert service["platform"] == "linux/amd64"
    assert not service.get("devices")
    reservations = service.get("deploy", {}).get("resources", {}).get("reservations", {})
    assert not reservations.get("devices")
    assert service["volumes"][0]["target"] == "/data"
