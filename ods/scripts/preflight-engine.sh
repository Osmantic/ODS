#!/usr/bin/env bash
set -euo pipefail

REPORT_FILE="/tmp/ods-preflight-report.json"
TIER="${TIER:-1}"
RAM_GB="${RAM_GB:-0}"
DISK_GB="${DISK_GB:-0}"
GPU_BACKEND="${GPU_BACKEND:-nvidia}"
GPU_VRAM_MB="${GPU_VRAM_MB:-0}"
GPU_NAME="${GPU_NAME:-Unknown}"
PLATFORM_ID="${PLATFORM_ID:-linux}"
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STRICT="false"
ENV_MODE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --report)
            REPORT_FILE="${2:-$REPORT_FILE}"
            shift 2
            ;;
        --tier)
            TIER="${2:-$TIER}"
            shift 2
            ;;
        --ram-gb)
            RAM_GB="${2:-$RAM_GB}"
            shift 2
            ;;
        --disk-gb)
            DISK_GB="${2:-$DISK_GB}"
            shift 2
            ;;
        --gpu-backend)
            GPU_BACKEND="${2:-$GPU_BACKEND}"
            shift 2
            ;;
        --gpu-vram-mb)
            GPU_VRAM_MB="${2:-$GPU_VRAM_MB}"
            shift 2
            ;;
        --gpu-name)
            GPU_NAME="${2:-$GPU_NAME}"
            shift 2
            ;;
        --platform-id)
            PLATFORM_ID="${2:-$PLATFORM_ID}"
            shift 2
            ;;
        --compose-overlays)
            COMPOSE_OVERLAYS="${2:-$COMPOSE_OVERLAYS}"
            shift 2
            ;;
        --script-dir)
            SCRIPT_DIR="${2:-$SCRIPT_DIR}"
            shift 2
            ;;
        --strict)
            STRICT="true"
            shift
            ;;
        --env)
            ENV_MODE="true"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

ROOT_DIR="$SCRIPT_DIR"
PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
    . "$ROOT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

"$PYTHON_CMD" - "$REPORT_FILE" "$TIER" "$RAM_GB" "$DISK_GB" "$GPU_BACKEND" "$GPU_VRAM_MB" "$GPU_NAME" "$PLATFORM_ID" "$COMPOSE_OVERLAYS" "$SCRIPT_DIR" "$ENV_MODE" "$STRICT" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

(
    report_file,
    tier,
    ram_gb,
    disk_gb,
    gpu_backend,
    gpu_vram_mb,
    gpu_name,
    platform_id,
    compose_overlays,
    script_dir,
    env_mode,
    strict_mode,
) = sys.argv[1:]

env_mode = env_mode == "true"
strict_mode = strict_mode == "true"

try:
    ram_gb = int(float(ram_gb))
except Exception:
    ram_gb = 0
try:
    disk_gb = int(float(disk_gb))
except Exception:
    disk_gb = 0
try:
    gpu_vram_mb = int(float(gpu_vram_mb))
except Exception:
    gpu_vram_mb = 0

tier_key = str(tier).upper()
tier_rank_map = {
    "0": 0,
    "CLOUD": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "T0": 0,
    "T1": 1,
    "T2": 2,
    "T3": 3,
    "T4": 4,
    "SH_COMPACT": 3,
    "SH_LARGE": 4,
}
tier_rank = tier_rank_map.get(tier_key, 1)

min_ram_map = {
    "0": 4,
    "CLOUD": 4,
    "T0": 4,
    "1": 16,
    "2": 32,
    "3": 48,
    "4": 64,
    "SH_COMPACT": 64,
    "SH_LARGE": 96,
}
min_disk_map = {
    "0": 15,
    # Cloud mode skips local GGUF downloads and local llama-server model
    # storage. It still needs room for core Docker images, configs, logs, and
    # optional services, but it should not inherit the 50GB local-model default.
    "CLOUD": 25,
    "T0": 15,
    "1": 30,
    "2": 50,
    "3": 80,
    "4": 150,
    "SH_COMPACT": 80,
    "SH_LARGE": 120,
}
min_ram = min_ram_map.get(tier_key, 16)
min_disk = min_disk_map.get(tier_key, 50)

checks = []

def add_check(check_id, status, message, action):
    checks.append(
        {
            "id": check_id,
            "status": status,
            "message": message,
            "action": action,
        }
    )

# Platform support check
if platform_id in {"linux", "wsl"}:
    add_check(
        "platform-support",
        "pass",
        f"Platform '{platform_id}' is currently supported by install-core.sh.",
        "",
    )
elif platform_id in {"macos", "windows"}:
    add_check(
        "platform-support",
        "warn",
        f"Platform '{platform_id}' is supported via installer MVP path (not full parity yet).",
        "Continue with platform installer and follow generated doctor report recommendations.",
    )
else:
    add_check(
        "platform-support",
        "blocker",
        f"Platform '{platform_id}' is not yet supported by install-core.sh.",
        "Use Linux/WSL path for now or run platform-specific installer once implemented.",
    )

# Compose overlay existence check
overlays = [o.strip() for o in compose_overlays.split(",") if o.strip()]
if overlays:
    missing = [o for o in overlays if not (pathlib.Path(script_dir) / o).exists()]
    if missing:
        add_check(
            "compose-overlays",
            "blocker",
            f"Compose overlays are missing: {', '.join(missing)}.",
            "Restore missing compose files or update capability profile overlay mapping.",
        )
    else:
        add_check(
            "compose-overlays",
            "pass",
            f"Compose overlays resolved: {', '.join(overlays)}.",
            "",
        )
else:
    add_check(
        "compose-overlays",
        "warn",
        "No compose overlays supplied from capability profile.",
        "Ensure CAP_COMPOSE_OVERLAYS is populated; installer will use legacy fallback.",
    )

# RAM and disk checks
if ram_gb >= min_ram:
    add_check(
        "memory",
        "pass",
        f"RAM {ram_gb}GB meets tier {tier_key} recommendation ({min_ram}GB).",
        "",
    )
else:
    add_check(
        "memory",
        "warn",
        f"RAM {ram_gb}GB is below tier {tier_key} recommendation ({min_ram}GB).",
        f"Use a lower tier or increase memory to at least {min_ram}GB.",
    )

if disk_gb >= min_disk:
    add_check(
        "disk",
        "pass",
        f"Disk {disk_gb}GB meets tier {tier_key} recommendation ({min_disk}GB).",
        "",
    )
else:
    add_check(
        "disk",
        "blocker",
        f"Disk {disk_gb}GB is below required minimum for tier {tier_key} ({min_disk}GB).",
        f"Free at least {min_disk - disk_gb}GB or choose a smaller tier.",
    )

# GPU checks
gpu_backend = (gpu_backend or "").lower()
if gpu_backend == "amd":
    add_check(
        "gpu-backend",
        "pass",
        f"AMD backend selected ({gpu_name}).",
        "",
    )
elif gpu_backend == "nvidia":
    if gpu_name.strip().lower() in {"none", ""} or gpu_vram_mb <= 0:
        add_check(
            "gpu-vram",
            "warn",
            "NVIDIA backend selected but no NVIDIA GPU VRAM was detected.",
            "Install/verify NVIDIA drivers or switch to a supported AMD path.",
        )
    elif tier_rank >= 2 and gpu_vram_mb < 10000:
        add_check(
            "gpu-vram",
            "warn",
            f"NVIDIA VRAM {gpu_vram_mb}MB is below recommended floor for tier {tier_key}.",
            "Use tier 1 or a GPU with at least 12GB VRAM for better performance.",
        )
    else:
        add_check(
            "gpu-vram",
            "pass",
            f"NVIDIA backend selected ({gpu_name}, {gpu_vram_mb}MB VRAM).",
            "",
        )
elif gpu_backend == "apple":
    add_check(
        "gpu-backend",
        "warn",
        "Apple backend selected (experimental path).",
        "Use macOS installer preflight + doctor and run reduced profile set until Tier A parity is complete.",
    )
elif gpu_backend == "cpu":
    if platform_id in {"windows", "macos"}:
        add_check(
            "gpu-backend",
            "warn",
            "CPU fallback selected on non-Linux platform.",
            "Use reduced model/profile defaults; expect slower inference.",
        )
    else:
        add_check(
            "gpu-backend",
            "warn",
            "CPU fallback selected.",
            "Install/verify GPU drivers for best performance or continue with small models.",
        )
else:
    add_check(
        "gpu-backend",
        "warn",
        f"Unknown backend '{gpu_backend}'.",
        "Verify capability profile and hardware detection output.",
    )

blockers = [c for c in checks if c["status"] == "blocker"]
warnings = [c for c in checks if c["status"] == "warn"]

report = {
    "version": "1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "inputs": {
        "tier": tier_key,
        "ram_gb": ram_gb,
        "disk_gb": disk_gb,
        "gpu_backend": gpu_backend,
        "gpu_vram_mb": gpu_vram_mb,
        "gpu_name": gpu_name,
        "platform_id": platform_id,
        "compose_overlays": overlays,
        "script_dir": script_dir,
    },
    "summary": {
        "checks": len(checks),
        "blockers": len(blockers),
        "warnings": len(warnings),
        "can_proceed": len(blockers) == 0,
    },
    "checks": checks,
}

report_path = pathlib.Path(report_file)
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

if env_mode:
    def out(key, value):
        safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
        print(f'{key}="{safe}"')

    out("PREFLIGHT_REPORT_FILE", str(report_path))
    out("PREFLIGHT_CHECK_COUNT", report["summary"]["checks"])
    out("PREFLIGHT_BLOCKERS", report["summary"]["blockers"])
    out("PREFLIGHT_WARNINGS", report["summary"]["warnings"])
    out("PREFLIGHT_CAN_PROCEED", str(report["summary"]["can_proceed"]).lower())

if strict_mode and blockers:
    raise SystemExit(1)
PY
