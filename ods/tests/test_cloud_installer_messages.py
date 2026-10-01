"""Cloud messages must not claim local CPU inference or recommend GPU drivers.

Only the real zero-GPU logging branch and read-only preflight engine execute.
No installer phase, Docker, hardware detection, or host services are launched.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(os.environ.get("ODS_CLOUD_MESSAGE_SOURCE", Path(__file__).parents[1]))


@pytest.mark.parametrize("mode", ["cloud", "local"])
def test_zero_gpu_message_preserves_selected_backend(mode):
    source = (ROOT / "installers/phases/03-features.sh").read_text()
    start = source.index('if [[ "${GPU_COUNT:-0}" -eq 0 ]]; then')
    end = source.index("# An external model", start)
    # Execute precisely the existing branch, not an alternative reimplementation.
    branch = source[start:end]
    program = """set -euo pipefail
log() { printf '%s\\n' "$*"; }
GPU_COUNT=0 GPU_BACKEND=cpu
ODS_MODE="$1"
branch() {
""" + branch + """
}
branch
printf 'STATE:%s:%s:%s\\n' "$ODS_MODE" "$GPU_COUNT" "$GPU_BACKEND"
"""
    result = subprocess.run(["bash", "-c", program, "cloud-message", mode],
                            capture_output=True, text=True, check=True, timeout=5)
    assert f"STATE:{mode}:0:cpu" in result.stdout
    if mode == "cloud":
        assert "Cloud mode" in result.stdout
        assert "GPU detection was skipped" in result.stdout
        assert "CPU-only" not in result.stdout
        assert "No GPU detected" not in result.stdout
    else:
        assert "No GPU detected — skipping GPU assignment (CPU-only mode)." in result.stdout


def preflight(tmp_path, tier, backend, *, ram=64, disk=200):
    (tmp_path / "docker-compose.base.yml").touch()
    report = tmp_path / "report.json"
    result = subprocess.run([
        "bash", str(ROOT / "scripts/preflight-engine.sh"),
        "--report", str(report), "--tier", tier, "--ram-gb", str(ram),
        "--disk-gb", str(disk), "--gpu-backend", backend, "--gpu-vram-mb", "0",
        "--gpu-name", "None", "--platform-id", "wsl",
        "--compose-overlays", "docker-compose.base.yml", "--script-dir", str(tmp_path),
    ], capture_output=True, text=True, timeout=10,
        env={**os.environ, "PATH": "/usr/bin:/bin", "LEMONADE_EXTERNAL": "false"})
    assert report.exists(), result.stderr
    return json.loads(report.read_text())


@pytest.mark.parametrize("backend", ["cpu", "nvidia", "amd", "apple"])
def test_cloud_gpu_check_is_not_a_local_inference_warning(tmp_path, backend):
    report = preflight(tmp_path, "CLOUD", backend)
    checks = {value["id"]: value for value in report["checks"]}
    gpu = checks["gpu-backend"]
    assert gpu["status"] == "pass"
    assert "Cloud model inference" in gpu["message"]
    assert "does not require a local GPU" in gpu["message"]
    assert gpu["action"] == ""
    assert report["inputs"]["gpu_backend"] == backend
    assert report["summary"]["warnings"] == 0


def test_cloud_keeps_real_memory_and_disk_problems(tmp_path):
    report = preflight(tmp_path, "CLOUD", "cpu", ram=1, disk=1)
    checks = {value["id"]: value for value in report["checks"]}
    assert checks["memory"]["status"] == "warn"
    assert checks["disk"]["status"] == "blocker"
    assert checks["gpu-backend"]["status"] == "pass"
    assert report["summary"]["warnings"] == 1
    assert report["summary"]["blockers"] == 1


@pytest.mark.parametrize("backend,check_id,message", [
    ("cpu", "gpu-backend", "CPU fallback selected."),
    ("nvidia", "gpu-vram", "no NVIDIA GPU VRAM was detected"),
])
def test_local_gpu_warnings_remain(tmp_path, backend, check_id, message):
    report = preflight(tmp_path, "2", backend)
    check = next(value for value in report["checks"] if value["id"] == check_id)
    assert check["status"] == "warn"
    assert message in check["message"]
    assert check["action"]
