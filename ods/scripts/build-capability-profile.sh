#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_FILE=""
ENV_MODE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT_FILE="${2:-}"
            shift 2
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

if [[ -z "$OUTPUT_FILE" ]]; then
    OUTPUT_FILE="${ROOT_DIR}/.capabilities.json"
fi

if [[ ! -x "${SCRIPT_DIR}/detect-hardware.sh" ]]; then
    echo "detect-hardware.sh not found or not executable" >&2
    exit 1
fi

[[ -f "$ROOT_DIR/lib/safe-env.sh" ]] && . "$ROOT_DIR/lib/safe-env.sh"

HARDWARE_JSON="$("${SCRIPT_DIR}/detect-hardware.sh" --json)"

PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
    . "$ROOT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

CLASS_ENV="$("${SCRIPT_DIR}/classify-hardware.sh" \
    --platform-id "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('os','unknown'))" "$HARDWARE_JSON")" \
    --gpu-vendor "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('gpu',{}).get('type','unknown'))" "$HARDWARE_JSON")" \
    --memory-type "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('gpu',{}).get('memory_type','unknown'))" "$HARDWARE_JSON")" \
    --vram-mb "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('gpu',{}).get('vram_mb',0))" "$HARDWARE_JSON")" \
    --device-id "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('gpu',{}).get('device_id',''))" "$HARDWARE_JSON")" \
    --gpu-name "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('gpu',{}).get('name',''))" "$HARDWARE_JSON")" \
    --cpu-name "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('cpu',''))" "$HARDWARE_JSON")" \
    --ram-mb "$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('ram_gb',0) * 1024)" "$HARDWARE_JSON")" \
    --env)"
load_env_from_output <<< "$CLASS_ENV"

# Source service registry for LLM port
if [[ -f "$ROOT_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$ROOT_DIR"
    . "$ROOT_DIR/lib/service-registry.sh"
    sr_load
    [[ -f "$ROOT_DIR/lib/safe-env.sh" ]] && . "$ROOT_DIR/lib/safe-env.sh"
    [[ -f "$ROOT_DIR/.env" ]] && load_env_file "$ROOT_DIR/.env" && sr_resolve_ports
fi
_LLM_PORT="${SERVICE_PORTS[llama-server]:-11434}"
_LLM_HEALTH="${SERVICE_HEALTH[llama-server]:-/health}"

"$PYTHON_CMD" - "$HARDWARE_JSON" "$OUTPUT_FILE" "$ENV_MODE" "${HW_CLASS_ID:-unknown}" "${HW_CLASS_LABEL:-Unknown}" "${HW_REC_BACKEND:-cpu}" "${HW_REC_TIER:-T1}" "${HW_REC_COMPOSE_OVERLAYS:-}" "$_LLM_PORT" "$_LLM_HEALTH" <<'PY'
import json
import os
import pathlib
import sys

hardware = json.loads(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
env_mode = sys.argv[3] == "true"
hw_class_id = sys.argv[4]
hw_class_label = sys.argv[5]
hw_rec_backend = sys.argv[6]
hw_rec_tier = sys.argv[7]
hw_rec_overlays = [x for x in sys.argv[8].split(",") if x]
llm_port = int(sys.argv[9]) if len(sys.argv) > 9 else 11434
llm_health = sys.argv[10] if len(sys.argv) > 10 else "/health"

os_name = (hardware.get("os") or "unknown").lower()
if os_name in {"linux", "wsl"}:
    family = "linux"
elif os_name == "macos":
    family = "darwin"
elif os_name == "windows":
    family = "windows"
else:
    family = "unknown"

gpu = hardware.get("gpu", {})
gpu_type = (gpu.get("type") or "none").lower()
gpu_name = gpu.get("name") or "None"
memory_type = (gpu.get("memory_type") or "none").lower()
vram_mb = int(gpu.get("vram_mb") or 0)
gpu_count = int(gpu.get("count") or (1 if gpu_type not in {"none", ""} else 0))
experimental_jetson = os.environ.get("ODS_ENABLE_EXPERIMENTAL_JETSON") == "1"

llm_health_url = f"http://localhost:{llm_port}{llm_health}"
llm_api_port = llm_port

if gpu_type == "amd" and memory_type == "unified":
    llm_backend = "amd"
    overlays = ["docker-compose.base.yml", "docker-compose.amd.yml"]
elif gpu_type == "nvidia":
    llm_backend = "nvidia"
    overlays = ["docker-compose.base.yml", "docker-compose.nvidia.yml"]
elif gpu_type == "jetson" and experimental_jetson:
    # Jetson is Tegra (arm64 + iGPU + unified memory). The dedicated overlay
    # docker-compose.jetson.yml lands in milestone 1 phase 3; until then fall
    # back to the cpu overlay so the installer pipeline stays valid on Jetson
    # hosts without claiming a runtime path that doesn't exist yet.
    llm_backend = "jetson"
    overlays = ["docker-compose.base.yml", "docker-compose.cpu.yml"]
elif gpu_type == "apple":
    llm_backend = "apple"
    overlays = ["docker-compose.base.yml", "docker-compose.amd.yml"]
else:
    llm_backend = "cpu"
    overlays = ["docker-compose.base.yml", "docker-compose.cpu.yml"]

tier = (hardware.get("tier") or "T1").upper()
if tier in {"T1", "T2", "T3", "T4"}:
    recommended = tier
elif tier in {"SH_COMPACT", "SH_LARGE"}:
    recommended = tier
else:
    recommended = "T1"

if hw_rec_tier:
    recommended = hw_rec_tier
if hw_rec_backend:
    llm_backend = hw_rec_backend
if hw_rec_overlays:
    overlays = hw_rec_overlays

profile = {
    "version": "1",
    "platform": {
        "id": os_name,
        "family": family,
    },
    "gpu": {
        "vendor": gpu_type if gpu_type in {"nvidia", "amd", "apple", "none"} else "unknown",
        "name": gpu_name,
        "memory_type": memory_type if memory_type in {"discrete", "unified", "none"} else "unknown",
        "count": gpu_count,
        "vram_mb": vram_mb,
    },
    "runtime": {
        "llm_backend": llm_backend,
        "llm_health_url": llm_health_url,
        "llm_api_port": llm_api_port,
    },
    "compose": {
        "overlays": overlays,
    },
    "tier": {
        "recommended": recommended,
    },
    "hardware_class": {
        "id": hw_class_id,
        "label": hw_class_label,
    }
}

output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")

if env_mode:
    env = {
        "CAP_PROFILE_VERSION": profile["version"],
        "CAP_PLATFORM_ID": profile["platform"]["id"],
        "CAP_PLATFORM_FAMILY": profile["platform"]["family"],
        "CAP_GPU_VENDOR": profile["gpu"]["vendor"],
        "CAP_GPU_NAME": profile["gpu"]["name"],
        "CAP_GPU_MEMORY_TYPE": profile["gpu"]["memory_type"],
        "CAP_GPU_COUNT": str(profile["gpu"]["count"]),
        "CAP_GPU_VRAM_MB": str(profile["gpu"]["vram_mb"]),
        "CAP_LLM_BACKEND": profile["runtime"]["llm_backend"],
        "CAP_LLM_HEALTH_URL": profile["runtime"]["llm_health_url"],
        "CAP_LLM_API_PORT": str(profile["runtime"]["llm_api_port"]),
        "CAP_RECOMMENDED_TIER": profile["tier"]["recommended"],
        "CAP_COMPOSE_OVERLAYS": ",".join(profile["compose"]["overlays"]),
        "CAP_HARDWARE_CLASS_ID": profile["hardware_class"]["id"],
        "CAP_HARDWARE_CLASS_LABEL": profile["hardware_class"]["label"],
        "CAP_PROFILE_FILE": str(output_path),
    }
    for key, value in env.items():
        safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
        print(f'{key}="{safe}"')
PY
