#!/usr/bin/env bash
# A Windows model server owns the host GPU when Windows setup passes
# --native-llm-gpu-name with the native route (today the AMD GPU on the AMD
# route). WSL then sees no GPU on purpose, so phase 02 must not run the NVIDIA
# driver repair, which in WSL only prints "NVIDIA GPU access is unavailable in
# WSL". This checks the predicate (installers/lib/native-llm.sh), the fallback
# isolated sourcing of phase 02 gets, and the real phase 02 driver repair
# block that uses it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/02-detection.sh"
GPU='AMD Radeon RX 9070 XT'

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

# shellcheck source=../../installers/lib/native-llm.sh
source "$ROOT_DIR/installers/lib/native-llm.sh"

owns() (
    NATIVE_LLM_BASE_URL="$1"
    NATIVE_LLM_GPU_NAME="$2"
    if ods_native_llm_owns_host_gpu; then echo yes; else echo no; fi
)
[[ "$(owns http://localhost:8080 "$GPU")" == yes ]] || fail "the native route with a Windows GPU name owns the host GPU"
[[ "$(owns http://localhost:8080 '')" == no ]] || fail "a native route without a Windows GPU name does not claim it"
[[ "$(owns '' "$GPU")" == no ]] || fail "a GPU name without the native route does not claim it"
[[ "$(owns '' '')" == no ]] || fail "no native route, no claim"
pass "ods_native_llm_owns_host_gpu: the native route plus the Windows GPU name, nothing else"

# The definition an isolated phase 02 gets agrees with it.
phase_fallback="$(awk '/^declare -F ods_native_llm_owns_host_gpu/{p=1} p{print} p && /^    \|\|/{exit}' "$PHASE")"
[[ "$phase_fallback" == *'ods_native_llm_requested && [[ -n "${NATIVE_LLM_GPU_NAME:-}" ]]'* ]] \
    || fail "phase 02's fallback predicate must match installers/lib/native-llm.sh"
pass "isolated phase 02 defines the same predicate"

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
[[ "$(boot http://localhost:8080 "$GPU")" == no ]] || fail "the driver repair ran for a GPU the Windows server owns"
[[ "$(boot '' '')" == yes ]] || fail "a host without a GPU still gets the driver repair"
[[ "$(boot http://localhost:8080 '')" == yes ]] || fail "a native route without the Windows GPU name still gets the driver repair"
pass "phase 02 runs no NVIDIA driver repair for a GPU a Windows model server owns"

echo "native LLM GPU owner: all checks passed"
