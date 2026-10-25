"""Run phase 06's GPU settings producer and heredoc against the same .env.

The output must replace its input, as in the installer: using separate files
would hide lookups that happen after the shell has truncated the owner's .env.
Only group discovery is stubbed; no installer host mutations are executed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PHASE = (ROOT / "installers/phases/06-directories.sh").read_text()


def render(root, backend, saved=None, target="gfx1151", runtime="rocm"):
    root.mkdir(exist_ok=True)
    output = root / ".env"
    if saved is not None:
        output.write_text(saved)
    start = PHASE.index("    _env_get() {")
    reader = PHASE[start:PHASE.index("\n    }", start) + len("\n    }")]
    marker = "    # Resolve GPU runtime overrides before opening .env"
    producer = ""
    if marker in PHASE:
        start = PHASE.index(marker)
        producer = PHASE[start:PHASE.index("    # Generate .env file", start)]
    start = PHASE.index('$(if [[ "$GPU_BACKEND" == "amd" ]]; then',
                        PHASE.index('cat > "$INSTALL_DIR/.env" << ENV_EOF'))
    template = PHASE[start:PHASE.index("#=== Ports ===", start)]
    script = f'''set -euo pipefail
source "$1/lib/safe-env.sh"
source "$1/lib/dotenv-quote.sh"
source "$1/installers/lib/amd-runtime.sh"
INSTALL_DIR="$2"
_env_existing="$INSTALL_DIR/.env"
GPU_BACKEND="$3"
AMD_GFX_TARGET="$4"
AMD_INFERENCE_BACKEND_VALUE="$5"
getent() {{
    case "$2" in
        video) printf 'video:x:44:\\n' ;;
        render) printf 'render:x:992:\\n' ;;
        *) return 1 ;;
    esac
}}
{reader}
{producer}
cat > "$INSTALL_DIR/.env" << ENV_EOF
{template}ENV_EOF
'''
    environment = {key: value for key, value in os.environ.items() if key not in {
        "HSA_OVERRIDE_GFX_VERSION", "ROCBLAS_USE_HIPBLASLT", "ONEAPI_DEVICE_SELECTOR"}}
    subprocess.run(["bash", "-c", script, "gpu-env-rerun", str(ROOT), str(root),
                    backend, target, runtime], env=environment, check=True,
                   text=True, capture_output=True)
    return output


@pytest.mark.parametrize("backend,saved,expected", [
    ("amd", 'HSA_OVERRIDE_GFX_VERSION="11.0.0" # owner override\nROCBLAS_USE_HIPBLASLT=1\n',
     {"HSA_OVERRIDE_GFX_VERSION": "11.0.0", "ROCBLAS_USE_HIPBLASLT": "1"}),
    ("amd", "ROCBLAS_USE_HIPBLASLT=0\n", {"ROCBLAS_USE_HIPBLASLT": "0"}),
    ("sycl", 'ONEAPI_DEVICE_SELECTOR="level_zero:0" # one GPU\n',
     {"ONEAPI_DEVICE_SELECTOR": "level_zero:0"}),
])
def test_owner_gpu_settings_survive_repeated_reruns(tmp_path, backend, saved, expected):
    for rerun in range(2):
        output = render(tmp_path, backend, saved if rerun == 0 else None)
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()
                      if line and not line.startswith("#"))
        for key, value in expected.items():
            assert values.get(key) == value


@pytest.mark.parametrize("backend,target,runtime,expected", [
    ("amd", "gfx1151", "rocm", {}),
    ("amd", "gfx1031", "rocm", {"HSA_OVERRIDE_GFX_VERSION": "10.3.0"}),
    ("amd", "gfx1031", "vulkan", {}),
    ("sycl", "", "", {"ONEAPI_DEVICE_SELECTOR": "level_zero:gpu"}),
    ("cpu", "", "", {}),
])
def test_clean_install_gpu_defaults(tmp_path, backend, target, runtime, expected):
    output = render(tmp_path, backend, target=target, runtime=runtime)
    settings = {line.split("=", 1)[0]: line.split("=", 1)[1]
                for line in output.read_text().splitlines()
                if line.startswith(("HSA_OVERRIDE_GFX_VERSION=", "ROCBLAS_USE_HIPBLASLT=",
                                    "ONEAPI_DEVICE_SELECTOR="))}
    assert settings == expected


@pytest.mark.parametrize("backend,overlay,saved,expected", [
    ("amd", "amd-rocm", "HSA_OVERRIDE_GFX_VERSION=11.0.0\nROCBLAS_USE_HIPBLASLT=1\n",
     {"HSA_OVERRIDE_GFX_VERSION": "11.0.0", "ROCBLAS_USE_HIPBLASLT": "1"}),
    ("sycl", "intel", "ONEAPI_DEVICE_SELECTOR=level_zero:0\n",
     {"ONEAPI_DEVICE_SELECTOR": "level_zero:0"}),
])
def test_rerun_settings_reach_shipped_compose(tmp_path, backend, overlay, saved, expected):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is unavailable; preservation tests still execute")
    output = render(tmp_path, backend, saved)
    environment = {key: value for key, value in os.environ.items() if key not in expected}
    environment["WEBUI_SECRET"] = "gpu-compose-test-only"
    result = subprocess.run(["docker", "compose", "--env-file", str(output),
                             "-f", str(ROOT / "docker-compose.base.yml"),
                             "-f", str(ROOT / f"docker-compose.{overlay}.yml"),
                             "config", "--format", "json"], env=environment,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    delivered = json.loads(result.stdout)["services"]["llama-server"]["environment"]
    for key, value in expected.items():
        assert delivered[key] == value
