#!/bin/bash
# =============================================================================
# Test: Linux NVIDIA Whisper acceleration is gated independently from the LLM
# =============================================================================

set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

log() { :; }
warn() { :; }
# shellcheck disable=SC1091
source "$ODS_ROOT/installers/lib/detection.sh"

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (expected '$expected', got '$actual')"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Driver 573 keeps NVIDIA LLM but forces only Whisper to CPU ==="
WHISPER_ACCELERATION=cuda
WHISPER_IMAGE=ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda
AUDIO_STT_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2
ODS_SKIP_GPU_OVERLAYS=""
ods_configure_whisper_acceleration nvidia 573
assert_eq "acceleration" "cpu" "$WHISPER_ACCELERATION"
assert_eq "forced fallback" "true" "$WHISPER_ACCELERATION_FORCED_CPU"
assert_eq "CPU image" "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu" "$WHISPER_IMAGE"
assert_eq "CPU model" "Systran/faster-whisper-base" "$AUDIO_STT_MODEL"
assert_eq "only Whisper overlay skipped" "whisper" "$ODS_SKIP_GPU_OVERLAYS"

echo ""
echo "=== Driver 575 defaults Whisper to CUDA ==="
unset WHISPER_ACCELERATION WHISPER_IMAGE AUDIO_STT_MODEL ODS_SKIP_GPU_OVERLAYS
ods_configure_whisper_acceleration nvidia 575
assert_eq "acceleration" "cuda" "$WHISPER_ACCELERATION"
assert_eq "not forced" "false" "$WHISPER_ACCELERATION_FORCED_CPU"
assert_eq "no overlay suppression" "" "${ODS_SKIP_GPU_OVERLAYS:-}"

echo ""
echo "=== Explicit CPU Whisper remains valid on a supported driver ==="
WHISPER_ACCELERATION=cpu
unset WHISPER_IMAGE AUDIO_STT_MODEL ODS_SKIP_GPU_OVERLAYS
ods_configure_whisper_acceleration nvidia 600
assert_eq "explicit CPU" "cpu" "$WHISPER_ACCELERATION"
assert_eq "CPU image selected" "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu" "$WHISPER_IMAGE"
assert_eq "overlay suppressed" "whisper" "$ODS_SKIP_GPU_OVERLAYS"

echo ""
echo "=== Resolver honors persisted CPU acceleration ==="
CPU_FLAGS=$(ODS_SKIP_GPU_OVERLAYS= WHISPER_ACCELERATION=cpu "$ODS_ROOT/scripts/resolve-compose-stack.sh" \
    --script-dir "$ODS_ROOT" --tier 1 --gpu-backend nvidia --gpu-count 1)
CUDA_FLAGS=$(ODS_SKIP_GPU_OVERLAYS= WHISPER_ACCELERATION=cuda "$ODS_ROOT/scripts/resolve-compose-stack.sh" \
    --script-dir "$ODS_ROOT" --tier 1 --gpu-backend nvidia --gpu-count 1)
if [[ "$CPU_FLAGS" == *"extensions/services/whisper/compose.yaml"* \
    && "$CPU_FLAGS" != *"extensions/services/whisper/compose.nvidia.yaml"* ]]; then
    echo "  PASS: CPU resolver keeps base Whisper without CUDA overlay"
    PASS=$((PASS + 1))
else
    echo "  FAIL: CPU resolver flags: $CPU_FLAGS"
    FAIL=$((FAIL + 1))
fi
if [[ "$CUDA_FLAGS" == *"extensions/services/whisper/compose.nvidia.yaml"* ]]; then
    echo "  PASS: CUDA resolver includes Whisper GPU overlay"
    PASS=$((PASS + 1))
else
    echo "  FAIL: CUDA resolver flags: $CUDA_FLAGS"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
