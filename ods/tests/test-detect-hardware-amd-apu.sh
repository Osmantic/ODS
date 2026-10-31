#!/bin/bash
# ============================================================================
# Test: scripts/detect-hardware.sh classifies AMD APU vs discrete GPU
# ============================================================================
# detect_amd_sysfs() feeds gpu.memory_type into the capability profile, which
# overrides GPU_MEMORY_TYPE in installer phase 02. A discrete card reported as
# unified gets a model sized for system RAM instead of its VRAM. Builds fake
# /sys/class/drm trees and checks the is_apu field (4th) of the output.
#
# Run: bash tests/test-detect-hardware-amd-apu.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/detect-hardware.sh
. "$SCRIPT_DIR/scripts/detect-hardware.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
PASS=0
FAIL=0

# make_card NAME VRAM_BYTES GTT_BYTES METRICS_FORMAT(empty = no table)
make_card() {
    local dir="$TMP_ROOT/$1/card0/device"
    mkdir -p "$dir"
    echo 0x1002 > "$dir/vendor"
    echo 0x747e > "$dir/device"
    echo "$2" > "$dir/mem_info_vram_total"
    echo "$3" > "$dir/mem_info_gtt_total"
    # gpu_metrics header: structure_size (2 bytes), format_revision, content_revision
    [[ -n "$4" ]] && printf "\x78\x00\x$(printf '%02x' "$4")\x00" > "$dir/gpu_metrics"
    return 0
}

check() {
    local label="$1" root="$2" expected="$3" out is_apu
    out="$(ODS_DRM_SYS="$TMP_ROOT/$root" detect_amd_sysfs)"
    IFS='|' read -r _ _ _ is_apu _ <<< "$out"
    if [[ "$is_apu" == "$expected" ]]; then
        echo "PASS: $label"; PASS=$((PASS + 1))
    else
        echo "FAIL: $label (is_apu=$is_apu, expected $expected)"; FAIL=$((FAIL + 1))
    fi
}

GIB=1073741824
# RX 7800 XT (16 GB) on a 64 GB desktop: amdgpu default GTT is half of RAM.
make_card rx7800 $((16 * GIB)) $((32 * GIB)) 1
# Radeon PRO W7900 (48 GB VRAM) on a 32 GB host.
make_card w7900 $((48 * GIB)) $((16 * GIB)) 1
# Strix Halo: 512 MB carve-out, large GTT, APU metrics table (v3).
make_card strix $((512 * 1048576)) $((96 * GIB)) 3
# APU without a readable metrics table still falls back to sizes.
make_card apu_nometrics $((1 * GIB)) $((24 * GIB)) ""

check "discrete 16 GB card with 32 GB GTT is not an APU" rx7800 false
check "discrete 48 GB workstation card is not an APU" w7900 false
check "Strix Halo with v3 gpu_metrics is an APU" strix true
check "small VRAM + large GTT without gpu_metrics is an APU" apu_nometrics true

echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
