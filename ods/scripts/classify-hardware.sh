#!/usr/bin/env bash
set -euo pipefail

# ODS Hardware Classifier — Two-pass GPU matching
# Pass 1: Match known_gpus by device_id then name_patterns (gpu-database.json)
# Pass 2: Fall back to heuristic_classes (threshold-based, same as old hardware-classes.json)
#
# Accepts both old args (--platform-id, --gpu-vendor) and new args (--device-id, --gpu-name, --ram-mb)
# Output contract: HW_CLASS_ID, HW_CLASS_LABEL, HW_REC_BACKEND, HW_REC_TIER,
#                  HW_REC_COMPOSE_OVERLAYS, HW_BANDWIDTH_GBPS, HW_MEMORY_SOURCE, HW_GPU_LABEL

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
GPU_DB="${ROOT_DIR}/config/gpu-database.json"
ENV_MODE="false"
PLATFORM_ID="${PLATFORM_ID:-unknown}"
GPU_VENDOR="${GPU_VENDOR:-unknown}"
MEMORY_TYPE="${MEMORY_TYPE:-unknown}"
VRAM_MB="${VRAM_MB:-0}"
DEVICE_ID=""
GPU_NAME=""
CPU_NAME=""
RAM_MB="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform-id) PLATFORM_ID="${2:-$PLATFORM_ID}"; shift 2 ;;
        --gpu-vendor)  GPU_VENDOR="${2:-$GPU_VENDOR}"; shift 2 ;;
        --memory-type) MEMORY_TYPE="${2:-$MEMORY_TYPE}"; shift 2 ;;
        --vram-mb)     VRAM_MB="${2:-$VRAM_MB}"; shift 2 ;;
        --device-id)   DEVICE_ID="${2:-}"; shift 2 ;;
        --gpu-name)    GPU_NAME="${2:-}"; shift 2 ;;
        --cpu-name)    CPU_NAME="${2:-}"; shift 2 ;;
        --ram-mb)      RAM_MB="${2:-0}"; shift 2 ;;
        --env)         ENV_MODE="true"; shift ;;
        --db)          GPU_DB="${2:-$GPU_DB}"; shift 2 ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ ! -f "$GPU_DB" ]]; then
    echo "ERROR: GPU database not found: $GPU_DB" >&2
    exit 1
fi

PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
    . "$ROOT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

"$PYTHON_CMD" - "$GPU_DB" "$ENV_MODE" "$PLATFORM_ID" "$GPU_VENDOR" "$MEMORY_TYPE" "$VRAM_MB" "$DEVICE_ID" "$GPU_NAME" "$CPU_NAME" "$RAM_MB" <<'PY'
import json
import sys

db_path = sys.argv[1]
env_mode = sys.argv[2] == "true"
platform_id = sys.argv[3]
gpu_vendor = sys.argv[4]
memory_type = sys.argv[5]
vram_mb = int(float(sys.argv[6] or 0))
device_id = sys.argv[7]
gpu_name = sys.argv[8]
cpu_name = sys.argv[9]
ram_mb = int(float(sys.argv[10] or 0))

with open(db_path, "r", encoding="utf-8") as f:
    db = json.load(f)

# --- Compose overlay mapping (backend → default overlays) ---
# CPU backend uses cpu overlay: CPU-only llama.cpp image, no GPU reservation
OVERLAY_MAP = {
    "amd":    ["docker-compose.base.yml", "docker-compose.amd.yml"],
    "nvidia": ["docker-compose.base.yml", "docker-compose.nvidia.yml"],
    "jetson": ["docker-compose.base.yml", "docker-compose.jetson.yml"],
    "apple":  ["docker-compose.base.yml", "docker-compose.apple.yml"],
    "cpu":    ["docker-compose.base.yml", "docker-compose.cpu.yml"],
}

# --- Pass 1: Match known_gpus by device_id then name_patterns ---
selected = None
best_name_len = 0          # longest matching pattern wins (prevents "XT" matching "XTX")
best_id_vram_diff = None   # closest VRAM wins for device_id-only fallback
combined_name = f"{gpu_name} {cpu_name}".strip().lower()

for entry in db.get("known_gpus", []):
    match = entry.get("match", {})

    # Try device_id match (exact, most reliable)
    dev_ids = [d.lower() for d in match.get("device_ids", [])]
    id_matched = device_id.lower() in dev_ids if device_id else False

    # Try name_patterns match (case-insensitive substring against gpu_name + cpu_name)
    patterns = match.get("name_patterns", [])
    matched_patterns = [p for p in patterns if p.lower() in combined_name] if combined_name and patterns else []
    name_matched = len(matched_patterns) > 0
    match_len = max((len(p) for p in matched_patterns), default=0)

    if id_matched and name_matched:
        # Both match — prefer longest pattern to avoid "XT" matching "XTX"
        if match_len > best_name_len:
            selected = entry
            best_name_len = match_len
    elif id_matched and best_name_len == 0:
        # Device ID matched but name didn't — use VRAM proximity as tiebreaker
        entry_vram = entry.get("specs", {}).get("memory_mb", 0)
        if vram_mb > 0:
            diff = abs(entry_vram - vram_mb)
        else:
            # No VRAM info: prefer smallest card (under-provision is safe,
            # over-provision crashes the model loader)
            diff = entry_vram if entry_vram > 0 else float("inf")
        if best_id_vram_diff is None or diff < best_id_vram_diff:
            selected = entry
            best_id_vram_diff = diff
    elif name_matched and not selected:
        selected = entry
        best_name_len = match_len

# --- Pass 2: Heuristic fallback (threshold-based, top-down) ---
if not selected:
    for entry in db.get("heuristic_classes", []):
        match = entry.get("match", {})

        # Check vendor
        m_vendor = match.get("vendor", "")
        if m_vendor and m_vendor != gpu_vendor:
            continue

        # Check memory_type
        m_memtype = match.get("memory_type", "")
        if m_memtype and m_memtype != memory_type:
            continue

        # Check min_vram_mb
        min_vram = match.get("min_vram_mb", -1)
        if min_vram >= 0 and vram_mb < min_vram:
            continue

        # Check max_vram_mb. This lets the database model "too small for
        # GPU inference" classes explicitly instead of catching them in broad
        # vendor fallbacks.
        max_vram = match.get("max_vram_mb", -1)
        if max_vram >= 0 and vram_mb > max_vram:
            continue

        # Check min_ram_mb (for unified memory classes)
        min_ram = match.get("min_ram_mb", -1)
        if min_ram >= 0 and ram_mb < min_ram:
            continue

        selected = entry
        break

# --- Bandwidth lookup ---
bandwidth = 0
if selected and "specs" in selected:
    bandwidth = selected["specs"].get("bandwidth_gbps", 0)

if bandwidth == 0 and gpu_name:
    # Search bandwidth table by substring match
    vendor_bw = db.get("known_gpu_bandwidth", {}).get(gpu_vendor, {})
    for bw_name, bw_val in vendor_bw.items():
        if bw_name.lower() in gpu_name.lower() or bw_name.lower() in cpu_name.lower():
            bandwidth = bw_val
            break

if bandwidth == 0:
    # Fall back to default bandwidth
    backend_key_map = {"nvidia": "cuda", "amd": "rocm", "apple": "metal"}
    bk = backend_key_map.get(gpu_vendor, "cpu_x86")
    bandwidth = db.get("defaults", {}).get("bandwidth_gbps", {}).get(bk, 0)

# --- Build result ---
if selected:
    # Known GPU entry
    if "specs" in selected:
        class_id = selected.get("id", "unknown")
        label = selected["specs"].get("label", selected.get("id", "Unknown"))
        rec = selected.get("recommended", {})
        backend = rec.get("backend", "cpu")
        tier = rec.get("tier", "T1")
        memory_source = selected["specs"].get("memory_source", "vram")
    else:
        # Heuristic class entry
        class_id = selected.get("id", "unknown")
        label = selected.get("id", "Unknown").replace("_", " ").title()
        rec = selected.get("recommended", {})
        backend = rec.get("backend", "cpu")
        tier = rec.get("tier", "T1")
        m_memtype = selected.get("match", {}).get("memory_type", "")
        memory_source = "ram" if backend == "cpu" or m_memtype == "unified" else "vram"
else:
    class_id = "unknown"
    label = "Unknown"
    backend = "cpu"
    tier = "T1"
    memory_source = "vram"

overlays = OVERLAY_MAP.get(backend, ["docker-compose.base.yml"])
# Darwin hosts running the apple backend use the canonical macOS overlay
# (installers/macos/docker-compose.macos.yml). The OVERLAY_MAP entry for
# "apple" still lists docker-compose.apple.yml so Linux hosts selecting
# --gpu-backend apple (Lemonade) continue to get the top-level overlay.
if backend == "apple" and platform_id == "macos":
    overlays = ["docker-compose.base.yml", "installers/macos/docker-compose.macos.yml"]
gpu_label = selected["specs"].get("label", "") if selected and "specs" in selected else ""

# --- Output ---
def out(key, value):
    safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
    print(f'{key}="{safe}"')

if env_mode:
    out("HW_CLASS_ID", class_id)
    out("HW_CLASS_LABEL", label)
    out("HW_REC_BACKEND", backend)
    out("HW_REC_TIER", tier)
    out("HW_REC_COMPOSE_OVERLAYS", ",".join(overlays))
    out("HW_BANDWIDTH_GBPS", bandwidth)
    out("HW_MEMORY_SOURCE", memory_source)
    out("HW_GPU_LABEL", gpu_label)
else:
    result = {
        "id": class_id,
        "label": label,
        "recommended": {
            "backend": backend,
            "tier": tier,
            "compose_overlays": overlays,
        },
        "bandwidth_gbps": bandwidth,
        "memory_source": memory_source,
        "gpu_label": gpu_label,
    }
    print(json.dumps(result, indent=2))
PY
