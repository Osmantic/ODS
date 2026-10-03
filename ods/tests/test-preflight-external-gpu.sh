#!/usr/bin/env bash
# Run the real requirements phase and engine against fixture files only.
# A complete external Lemonade GPU route replaces only the CPU warning.
# Variables below are consumed by the sourced requirements phase.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/source/scripts" "$tmp/source/lib"
cp "$ROOT/scripts/preflight-engine.sh" "$tmp/source/scripts/preflight-engine.sh"
cp "$ROOT/lib/safe-env.sh" "$tmp/source/lib/safe-env.sh"
chmod +x "$tmp/source/scripts/preflight-engine.sh"
touch "$tmp/source/docker-compose.base.yml"

run_case() (
    local label="$1" external="$2" url="$3" name="$4" vram="$5" gpu_status="$6"
    local backend="${7:-cpu}" ram="${8:-64}" disk="${9:-200}"
    SCRIPT_DIR="$tmp/source" INSTALL_DIR="$tmp/install"
    LOG_FILE="$tmp/requirements.log" PREFLIGHT_REPORT_FILE="$tmp/preflight.json"
    TIER=2 RAM_GB="$ram" DISK_AVAIL="$disk"
    GPU_BACKEND="$backend" GPU_VRAM=0 GPU_NAME=None GPU_COUNT=0
    INTERACTIVE=false DRY_RUN=true PYTHON_CMD=python3
    CAP_PLATFORM_ID=wsl CAP_COMPOSE_OVERLAYS=docker-compose.base.yml
    ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_COMFYUI=false
    EXTERNAL_LLM_URL='' ODS_MODE=lemonade
    # Deliberately not exported: phase 04 must pass this structured evidence.
    LEMONADE_EXTERNAL="$external" LEMONADE_BASE_URL="$url"
    LEMONADE_GPU_NAME="$name" LEMONADE_GPU_VRAM_MB="$vram"
    export -n LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_GPU_NAME LEMONADE_GPU_VRAM_MB
    declare -A SERVICE_PORTS=()
    tier_rank() { printf '2\n'; }
    ods_progress() { :; }; chapter() { :; }; log() { :; }
    ai_ok() { printf 'OK: %s\n' "$*"; }; ai_bad() { printf 'BAD: %s\n' "$*"; }
    ai_warn() { printf 'WARN: %s\n' "$*"; }; warn() { printf 'WARN: %s\n' "$*"; }
    # Prevent port/process inspection from touching the running installation.
    docker() { :; }; pgrep() { return 1; }; lsof() { return 1; }
    ss() { :; }; netstat() { :; }
    source "$ROOT/installers/phases/04-requirements.sh" >"$tmp/output"

    python3 - "$PREFLIGHT_REPORT_FILE" "$tmp/output" "$gpu_status" "$backend" \
        "$ram" "$disk" "$PREFLIGHT_WARNINGS" "$REQUIREMENTS_MET" <<'PY'
import json
import pathlib
import sys

report_path, output_path, status, backend, ram, disk, warnings, requirements_met = sys.argv[1:]
report = json.loads(pathlib.Path(report_path).read_text())
output = pathlib.Path(output_path).read_text()
checks = {check["id"]: check for check in report["checks"]}
gpu = checks["gpu-vram" if backend == "nvidia" else "gpu-backend"]
assert gpu["status"] == status, gpu
assert report["inputs"]["gpu_backend"] == backend, "local backend must remain unchanged"
if status == "pass":
    assert "External Lemonade GPU route configured" in gpu["message"], gpu
    assert report["inputs"]["external_gpu"] == {
        "provider": "lemonade", "gpu_name": "AMD Radeon RX 9070 XT", "gpu_vram_mb": 16304,
    }
    assert "CPU fallback selected" not in output, output
elif backend == "cpu":
    assert report["inputs"]["external_gpu"] is None
    assert "CPU fallback selected" in output, output
else:
    assert "no NVIDIA GPU VRAM was detected" in output, output

expected_warnings = int(status == "warn") + int(int(ram) < 32)
expected_blockers = int(int(disk) < 50)
assert report["summary"]["warnings"] == expected_warnings, report["summary"]
assert int(warnings) == expected_warnings, warnings
assert report["summary"]["blockers"] == expected_blockers, report["summary"]
assert requirements_met == str(expected_blockers == 0).lower(), requirements_met
if int(ram) < 32:
    assert checks["memory"]["status"] == "warn" and "RAM 16GB" in output, output
if int(disk) < 50:
    assert checks["disk"]["status"] == "blocker" and "Disk 10GB" in output, output
PY
    printf 'PASS: %s\n' "$label"
)

gpu_name='AMD Radeon RX 9070 XT'
endpoint='http://host.docker.internal:13305/api/v1'
run_case 'External AMD route replaces the CPU fallback warning' true "$endpoint" "$gpu_name" 16304 pass
run_case 'External route keeps RAM warnings and disk blockers' true "$endpoint" "$gpu_name" 16304 pass cpu 16 10
run_case 'Disabled external route retains CPU fallback warning' false "$endpoint" "$gpu_name" 16304 warn
run_case 'Missing external endpoint retains CPU fallback warning' true '' "$gpu_name" 16304 warn
run_case 'Missing GPU name retains CPU fallback warning' true "$endpoint" '' 16304 warn
run_case 'Unknown GPU name retains CPU fallback warning' true "$endpoint" Unknown 16304 warn
run_case 'Zero VRAM retains CPU fallback warning' true "$endpoint" "$gpu_name" 0 warn
run_case 'Invalid VRAM retains CPU fallback warning' true "$endpoint" "$gpu_name" 16GB warn
run_case 'Negative VRAM retains CPU fallback warning' true "$endpoint" "$gpu_name" -1 warn
run_case 'Other GPU warnings remain visible with an external route' true "$endpoint" "$gpu_name" 16304 warn nvidia
