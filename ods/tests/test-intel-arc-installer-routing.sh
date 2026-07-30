#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

json_get() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
for part in sys.argv[2].split("."):
    value = value[part]
if isinstance(value, bool):
    print(str(value).lower())
elif isinstance(value, list):
    print(",".join(str(item) for item in value))
else:
    print(value)
PY
}

assert_eq() {
    local expected="$1"
    local actual="$2"
    local label="$3"
    if [[ "$actual" != "$expected" ]]; then
        echo "[FAIL] $label: expected '$expected', got '$actual'" >&2
        exit 1
    fi
}

reset_fixture() {
    rm -rf "${TMP_DIR:?}/sys" "${TMP_DIR:?}/dev"
    mkdir -p "$TMP_DIR/sys" "$TMP_DIR/dev"
}

add_gpu() {
    local card="$1"
    local vendor="$2"
    local device="$3"
    local vram_bytes="$4"
    local name="$5"
    local card_dir="$TMP_DIR/sys/card${card}/device"
    mkdir -p "$card_dir"
    printf '%s\n' "$vendor" >"$card_dir/vendor"
    printf '%s\n' "$device" >"$card_dir/device"
    printf '%s\n' "$vram_bytes" >"$card_dir/lmem_total_bytes"
    printf '%s\n' "$vram_bytes" >"$card_dir/mem_info_vram_total"
    printf '0\n' >"$card_dir/mem_info_gtt_total"
    printf '%s\n' "$name" >"$card_dir/product_name"
}

build_profile() {
    local output="$1"
    local level_zero="$2"
    ODS_DRM_SYS="$TMP_DIR/sys" \
    ODS_DEV_DRI="$TMP_DIR/dev/dri" \
    ODS_LEVEL_ZERO_AVAILABLE="$level_zero" \
    ODS_DETECT_OS_OVERRIDE=linux \
        "$ROOT_DIR/scripts/build-capability-profile.sh" --output "$output"
}

echo "[intel] healthy Arc remains Intel and selects the SYCL compose backend"
reset_fixture
add_gpu 0 0x8086 0x56a0 17179869184 "Intel Arc A770"
mkdir -p "$TMP_DIR/dev/dri"
touch "$TMP_DIR/dev/dri/renderD128"
healthy_hardware="$TMP_DIR/intel-healthy-hardware.json"
ODS_DRM_SYS="$TMP_DIR/sys" \
ODS_DEV_DRI="$TMP_DIR/dev/dri" \
ODS_LEVEL_ZERO_AVAILABLE=1 \
ODS_DETECT_OS_OVERRIDE=linux \
    "$ROOT_DIR/scripts/detect-hardware.sh" --json >"$healthy_hardware"
assert_eq "intel" "$(json_get "$healthy_hardware" gpu.type)" "detected Arc type"
assert_eq "true" "$(json_get "$healthy_hardware" gpu.sycl_available)" "detected Arc SYCL readiness"
healthy_profile="$TMP_DIR/intel-healthy.json"
build_profile "$healthy_profile" 1

assert_eq "intel" "$(json_get "$healthy_profile" gpu.vendor)" "Arc vendor"
assert_eq "true" "$(json_get "$healthy_profile" gpu.sycl_available)" "Arc SYCL readiness"
assert_eq "intel" "$(json_get "$healthy_profile" runtime.llm_backend)" "Arc runtime backend"
assert_eq "ARC" "$(json_get "$healthy_profile" tier.recommended)" "Arc tier"
assert_eq "docker-compose.base.yml,docker-compose.intel.yml" \
    "$(json_get "$healthy_profile" compose.overlays)" "Arc compose overlays"

installer_detection="$(
    ODS_DRM_SYS="$TMP_DIR/sys" \
    ODS_DEV_DRI="$TMP_DIR/dev/dri" \
    ODS_LEVEL_ZERO_AVAILABLE=1 \
    ODS_DETECT_OS_OVERRIDE=linux \
    ROOT_DIR="$ROOT_DIR" \
        bash -s <<'SH'
set -euo pipefail
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE=/dev/null
log() { :; }
warn() { :; }
ai() { :; }
ai_warn() { :; }
# shellcheck source=/dev/null
source "$SCRIPT_DIR/installers/lib/detection.sh"
detect_gpu
ensure_intel_sycl_or_cpu_fallback
printf '%s|%s|%s\n' "$GPU_BACKEND" "$GPU_DEVICE_ID" "$GPU_VRAM"
SH
)"
assert_eq "intel|0x56a0|16384" "$installer_detection" "phase-02 Intel detection"

compose_env="$(
    "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        --script-dir "$ROOT_DIR" \
        --tier ARC \
        --gpu-backend intel \
        --profile-overlays "$(json_get "$healthy_profile" compose.overlays)" \
        --gpu-count 1 \
        --ods-mode local \
        --env
)"
grep -q 'docker-compose.intel.yml' <<<"$compose_env" \
    || { echo "[FAIL] Intel profile did not resolve the Intel compose overlay" >&2; exit 1; }
if grep -q 'docker-compose.nvidia.yml\|docker-compose.cpu.yml\|docker-compose.arc.yml' <<<"$compose_env"; then
    echo "[FAIL] Intel profile resolved an unintended accelerator overlay" >&2
    exit 1
fi

echo "[intel] missing Level Zero fails closed to CPU without losing hardware identity"
missing_runtime_profile="$TMP_DIR/intel-missing-runtime.json"
build_profile "$missing_runtime_profile" 0
assert_eq "intel" "$(json_get "$missing_runtime_profile" gpu.vendor)" "fallback preserves Arc vendor"
assert_eq "false" "$(json_get "$missing_runtime_profile" gpu.sycl_available)" "fallback SYCL readiness"
assert_eq "cpu" "$(json_get "$missing_runtime_profile" runtime.llm_backend)" "fallback runtime backend"
assert_eq "T1" "$(json_get "$missing_runtime_profile" tier.recommended)" "fallback tier"
assert_eq "docker-compose.base.yml,docker-compose.cpu.yml" \
    "$(json_get "$missing_runtime_profile" compose.overlays)" "fallback compose overlays"

installer_fallback="$(
    ODS_DRM_SYS="$TMP_DIR/sys" \
    ODS_DEV_DRI="$TMP_DIR/dev/dri" \
    ODS_LEVEL_ZERO_AVAILABLE=0 \
    ODS_DETECT_OS_OVERRIDE=linux \
    ROOT_DIR="$ROOT_DIR" \
        bash -s <<'SH'
set -euo pipefail
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE=/dev/null
log() { :; }
warn() { :; }
ai() { :; }
ai_warn() { :; }
# shellcheck source=/dev/null
source "$SCRIPT_DIR/installers/lib/detection.sh"
detect_gpu
ensure_intel_sycl_or_cpu_fallback
printf '%s|%s|%s\n' "$GPU_BACKEND" "$GPU_COUNT" "$GPU_VRAM"
SH
)"
assert_eq "cpu|0|0" "$installer_fallback" "phase-02 Intel fail-closed fallback"

echo "[intel] unsupported Intel devices and platforms do not claim Arc support"
reset_fixture
add_gpu 0 0x8086 0x9a49 1073741824 "Intel Iris Xe"
unsupported_profile="$TMP_DIR/intel-integrated.json"
build_profile "$unsupported_profile" 1
assert_eq "none" "$(json_get "$unsupported_profile" gpu.vendor)" "integrated Intel vendor"
assert_eq "cpu" "$(json_get "$unsupported_profile" runtime.llm_backend)" "integrated Intel backend"

wsl_classification="$(
    "$ROOT_DIR/scripts/classify-hardware.sh" \
        --platform-id wsl \
        --gpu-vendor intel \
        --memory-type discrete \
        --vram-mb 16384 \
        --device-id 0x56a0 \
        --gpu-name "Intel Arc A770"
)"
assert_eq "cpu" "$(
    python3 -c 'import json,sys; print(json.load(sys.stdin)["recommended"]["backend"])' \
        <<<"$wsl_classification"
)" "WSL Intel backend"

echo "[regression] AMD and CPU profiles keep their existing routes"
reset_fixture
add_gpu 0 0x1002 0x744c 17179869184 "AMD Radeon RX 7900 GRE"
amd_profile="$TMP_DIR/amd.json"
build_profile "$amd_profile" 0
assert_eq "amd" "$(json_get "$amd_profile" gpu.vendor)" "AMD vendor"
assert_eq "amd" "$(json_get "$amd_profile" runtime.llm_backend)" "AMD backend"
assert_eq "docker-compose.base.yml,docker-compose.amd.yml" \
    "$(json_get "$amd_profile" compose.overlays)" "AMD overlays"

reset_fixture
cpu_profile="$TMP_DIR/cpu.json"
build_profile "$cpu_profile" 0
assert_eq "none" "$(json_get "$cpu_profile" gpu.vendor)" "CPU vendor"
assert_eq "cpu" "$(json_get "$cpu_profile" runtime.llm_backend)" "CPU backend"
assert_eq "docker-compose.base.yml,docker-compose.cpu.yml" \
    "$(json_get "$cpu_profile" compose.overlays)" "CPU overlays"

echo "[PASS] Intel Arc installer routing contracts"
