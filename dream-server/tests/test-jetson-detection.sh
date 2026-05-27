#!/bin/bash
# ============================================================================
# Test: Jetson detection — detection.sh + detect-hardware.sh
# ============================================================================
# Builds a fake /etc/nv_tegra_release + /proc/device-tree/* tree and verifies:
#   1. installers/lib/detection.sh::detect_gpu() sets GPU_BACKEND=jetson with
#      unified-memory layout and a parsed JETSON_L4T_RELEASE.
#   2. scripts/detect-hardware.sh emits gpu.type=jetson in --json output.
#
# Run: bash tests/test-jetson-detection.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

# Minimal logging stubs so detection.sh can be sourced standalone.
log()  { :; }
warn() { :; }

# Source the module under test (detect_gpu)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/installers/lib/detection.sh"

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

assert_nonempty() {
    local label="$1" actual="$2"
    if [[ -n "$actual" ]]; then
        echo "  PASS: $label (= '$actual')"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (empty)"
        FAIL=$((FAIL + 1))
    fi
}

assert_match() {
    local label="$1" pattern="$2" actual="$3"
    if [[ "$actual" =~ $pattern ]]; then
        echo "  PASS: $label (matched /$pattern/)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (expected match /$pattern/, got '$actual')"
        FAIL=$((FAIL + 1))
    fi
}

# ----------------------------------------------------------------------------
# Build a minimal Jetson fixture tree
# ----------------------------------------------------------------------------
FIXTURE_DIR="$(mktemp -d -t dream-jetson-fixture-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

# /etc/nv_tegra_release format mirrors JetPack 6 / L4T R36.4.0
cat > "$FIXTURE_DIR/nv_tegra_release" <<'EOF'
# R36 (release), REVISION: 4.0, GCID: 41062509, BOARD: generic, EABI: aarch64, DATE: ...
EOF

# Device-tree files are NUL-terminated on real Jetson; mimic that here.
printf 'NVIDIA Jetson Orin Nano Developer Kit\0' > "$FIXTURE_DIR/dt_model"
printf 'nvidia,p3768-0000+p3767-0005\0nvidia,p3768-0000+p3767-0005\0nvidia,tegra234\0nvidia,tegra\0\0' > "$FIXTURE_DIR/dt_compatible"

mkdir -p "$FIXTURE_DIR/gpu.0"

echo "=== Testing detect_gpu() against Jetson fixture ==="
echo ""

# Reset relevant globals before each invocation.
unset GPU_BACKEND GPU_NAME GPU_VRAM GPU_COUNT GPU_DEVICE_ID GPU_MEMORY_TYPE JETSON_L4T_RELEASE

DREAM_UNAME_M="aarch64" \
DREAM_NV_TEGRA_RELEASE="$FIXTURE_DIR/nv_tegra_release" \
DREAM_DEVICE_TREE_COMPATIBLE="$FIXTURE_DIR/dt_compatible" \
DREAM_DEVICE_TREE_MODEL="$FIXTURE_DIR/dt_model" \
DREAM_GPU0_SYSFS="$FIXTURE_DIR/gpu.0" \
    detect_gpu

assert_eq "GPU_BACKEND"      "jetson"  "${GPU_BACKEND:-}"
assert_eq "GPU_MEMORY_TYPE"  "unified" "${GPU_MEMORY_TYPE:-}"
assert_eq "GPU_COUNT"        "1"       "${GPU_COUNT:-}"
assert_eq "GPU_NAME"         "NVIDIA Jetson Orin Nano Developer Kit" "${GPU_NAME:-}"
assert_eq "JETSON_L4T_RELEASE" "R36.4.0" "${JETSON_L4T_RELEASE:-}"
assert_eq "GPU_DEVICE_ID"    "R36.4.0" "${GPU_DEVICE_ID:-}"
assert_nonempty "GPU_VRAM"   "${GPU_VRAM:-}"

# ----------------------------------------------------------------------------
# Non-Jetson hosts must NOT trip the new branch.
# ----------------------------------------------------------------------------
echo ""
echo "=== Testing detect_gpu() on x86 host (must not detect jetson) ==="
echo ""

unset GPU_BACKEND GPU_NAME GPU_VRAM GPU_COUNT GPU_DEVICE_ID GPU_MEMORY_TYPE JETSON_L4T_RELEASE

# Point every Jetson signal at a path that doesn't exist AND override uname -m.
DREAM_UNAME_M="x86_64" \
DREAM_NV_TEGRA_RELEASE="/dev/null/does-not-exist" \
DREAM_DEVICE_TREE_COMPATIBLE="/dev/null/does-not-exist" \
DREAM_DEVICE_TREE_MODEL="/dev/null/does-not-exist" \
DREAM_GPU0_SYSFS="/dev/null/does-not-exist" \
DREAM_DRM_SYS="$FIXTURE_DIR/empty-drm" \
    detect_gpu || true

# On a non-Jetson host with empty drm and no nvidia-smi the detector falls
# back to cpu. The important assertion is that it didn't claim 'jetson'.
if [[ "${GPU_BACKEND:-}" == "jetson" ]]; then
    echo "  FAIL: x86 fallback wrongly detected as jetson"
    FAIL=$((FAIL + 1))
else
    echo "  PASS: x86 fallback did not claim jetson (backend=${GPU_BACKEND:-unset})"
    PASS=$((PASS + 1))
fi

# ----------------------------------------------------------------------------
# detect-hardware.sh JSON pipeline
# ----------------------------------------------------------------------------
echo ""
echo "=== Testing scripts/detect-hardware.sh --json with Jetson fixture ==="
echo ""

JSON_OUT=$(
    DREAM_UNAME_M="aarch64" \
    DREAM_NV_TEGRA_RELEASE="$FIXTURE_DIR/nv_tegra_release" \
    DREAM_DEVICE_TREE_COMPATIBLE="$FIXTURE_DIR/dt_compatible" \
    DREAM_DEVICE_TREE_MODEL="$FIXTURE_DIR/dt_model" \
    DREAM_GPU0_SYSFS="$FIXTURE_DIR/gpu.0" \
        bash "$SCRIPT_DIR/scripts/detect-hardware.sh" --json-compact 2>/dev/null
)

assert_match "gpu.type=jetson"        '"type":[[:space:]]*"jetson"'           "$JSON_OUT"
assert_match "gpu.architecture=tegra" '"architecture":[[:space:]]*"tegra"'    "$JSON_OUT"
assert_match "gpu.memory_type=unified" '"memory_type":[[:space:]]*"unified"'  "$JSON_OUT"
assert_match "gpu.name=Orin Nano"     'NVIDIA Jetson Orin Nano'               "$JSON_OUT"

# ----------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
