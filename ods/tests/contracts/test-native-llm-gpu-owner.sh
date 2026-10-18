#!/usr/bin/env bash
# A Windows model server owns the host GPU when Windows setup passes
# --native-llm-gpu-name with the native route: the AMD GPU on the AMD route,
# or an RTX Spark's N1X, which WSL still lists through nvidia-smi. Nothing in
# this stack may then claim that GPU. This checks the predicate
# (installers/lib/native-llm.sh), the fallbacks isolated sourcing gets
# (detection.sh, phase 02), and the real phase 02 blocks that use it: the
# capability-profile override and the NVIDIA driver repair. detect_gpu's own
# case is in tests/bats-tests/detection.bats.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/02-detection.sh"
SPARK='NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)'

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

# shellcheck source=../../installers/lib/native-llm.sh
source "$ROOT_DIR/installers/lib/native-llm.sh"

owns() (
    NATIVE_LLM_BASE_URL="$1"
    NATIVE_LLM_GPU_NAME="$2"
    if ods_native_llm_owns_host_gpu; then echo yes; else echo no; fi
)
[[ "$(owns http://localhost:8080 "$SPARK")" == yes ]] || fail "the native route with a Windows GPU name owns the host GPU"
[[ "$(owns http://localhost:8080 '')" == no ]] || fail "a native route without a Windows GPU name does not claim it"
[[ "$(owns '' "$SPARK")" == no ]] || fail "a GPU name without the native route does not claim it"
[[ "$(owns '' '')" == no ]] || fail "no native route, no claim"
pass "ods_native_llm_owns_host_gpu: the native route plus the Windows GPU name, nothing else"

# The definitions an isolated detection.sh or phase 02 gets agree with it.
fallback_owns() (
    unset -f ods_native_llm_owns_host_gpu ods_native_llm_requested
    log() { :; }; warn() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_bad() { :; }
    SCRIPT_DIR="$ROOT_DIR"
    # shellcheck source=../../installers/lib/detection.sh
    source "$ROOT_DIR/installers/lib/detection.sh"
    NATIVE_LLM_BASE_URL="$1"
    NATIVE_LLM_GPU_NAME="$2"
    if ods_native_llm_owns_host_gpu; then echo yes; else echo no; fi
)
for args in "http://localhost:8080|$SPARK|yes" "http://localhost:8080||no" "|$SPARK|no"; do
    IFS='|' read -r url name expected <<< "$args"
    [[ "$(fallback_owns "$url" "$name")" == "$expected" ]] || fail "detection.sh's fallback predicate differs for '$url' '$name'"
done
phase_fallback="$(awk '/^declare -F ods_native_llm_owns_host_gpu/{p=1} p{print} p && /^    \|\|/{exit}' "$PHASE")"
[[ "$phase_fallback" == *'ods_native_llm_requested && [[ -n "${NATIVE_LLM_GPU_NAME:-}" ]]'* ]] \
    || fail "phase 02's fallback predicate must match installers/lib/native-llm.sh"
pass "isolated detection.sh and phase 02 define the same predicate"

# Phase 02: the capability profile still sees the Windows server's GPU.
cap_block="$(awk '/^    # The capability profile still sees a GPU that a Windows model server/{p=1}
    /^    # AMD runs the llama.cpp image with Vulkan/{p=0} p' "$PHASE")"
[[ "$cap_block" == *'elif [[ "${CAP_PROFILE_LOADED:-false}" == "true" ]]; then'* ]] || fail "phase 02 capability override block not found"
cap() (
    log() { :; }
    # detect_gpu's result on this route: no GPU claimed inside WSL.
    GPU_BACKEND=cpu GPU_NAME='None (CPU-only mode)' GPU_VRAM=0 GPU_COUNT=0 GPU_MEMORY_TYPE=none
    CAP_PROFILE_LOADED=true CAP_LLM_BACKEND="$1" CAP_GPU_NAME="$2" CAP_GPU_VRAM_MB="$3" CAP_GPU_COUNT="$4" CAP_GPU_MEMORY_TYPE=discrete
    NATIVE_LLM_BASE_URL="$5"
    NATIVE_LLM_GPU_NAME="$6"
    eval "$cap_block"
    printf '%s|%s|%s|%s' "$GPU_BACKEND" "$GPU_NAME" "$GPU_VRAM" "$GPU_COUNT"
)
got="$(cap nvidia "$SPARK" 98240 1 http://localhost:8080 "$SPARK")"
[[ "$got" == 'cpu|None (CPU-only mode)|0|0' ]] || fail "the profile handed the Windows server's GPU to this stack: $got"
got="$(cap nvidia "$SPARK" 98240 1 http://localhost:8080 '')"
[[ "$got" == "nvidia|$SPARK|98240|1" ]] || fail "a native route without the Windows GPU name keeps the profile: $got"
got="$(cap nvidia "$SPARK" 98240 1 '' '')"
[[ "$got" == "nvidia|$SPARK|98240|1" ]] || fail "without the native route the profile decides as before: $got"
got="$(cap cpu None 0 0 http://localhost:8080 'AMD Radeon RX 9070 XT')"
[[ "$got" == 'cpu|None|0|0' ]] || fail "a CPU profile on the AMD native route applies exactly as before: $got"
pass "phase 02 never lets the capability profile claim a GPU a Windows model server owns; a CPU profile applies as before"

# Phase 02: no NVIDIA driver repair (and no WSL GPU warning) for that GPU.
boot_block="$(awk '/^# If detect_gpu found no working GPU, check if/{p=1} p{print} p && /^fi$/{exit}' "$PHASE")"
[[ "$boot_block" == *'fix_nvidia_secure_boot || true'* ]] || fail "phase 02 driver repair block not found"
boot() (
    called=no
    fix_nvidia_secure_boot() { called=yes; return 1; }
    GPU_BACKEND_FORCED_CPU=false GPU_COUNT=0 GPU_BACKEND=cpu DRY_RUN=false
    NATIVE_LLM_BASE_URL="$1"
    NATIVE_LLM_GPU_NAME="$2"
    eval "$boot_block"
    printf '%s' "$called"
)
[[ "$(boot http://localhost:8080 "$SPARK")" == no ]] || fail "the driver repair ran for a GPU the Windows server owns"
[[ "$(boot '' '')" == yes ]] || fail "a host without a GPU still gets the driver repair"
[[ "$(boot http://localhost:8080 '')" == yes ]] || fail "a native route without the Windows GPU name still gets the driver repair"
pass "phase 02 runs no NVIDIA driver repair for a GPU a Windows model server owns"

echo "native LLM GPU owner: all checks passed"
