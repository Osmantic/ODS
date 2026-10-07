#!/usr/bin/env bash
# Intel discrete Arc detection must not depend on lspci marketing strings —
# Battlemage enumerates as "Battlemage G31 [Intel Graphics]" (no "Arc" in the
# name) and lspci may not be installed. Device IDs are the contract:
#   Alchemist / DG2:  0x56a0-0x56bf, 0x5690-0x569f
#   Battlemage (BMG): 0xe2xx (B570/B580, Arc Pro B-series, BMG-G31)
# Integrated Xe iGPUs (Meteor Lake 0x7dxx, Raptor Lake 0xa7xx, ...) share
# system RAM and must NOT be picked up as inference GPUs.
#
# Fixtures are mock /sys/class/drm trees (ODS_DRM_SYS), matching
# test-amd-igpu-dgpu-selection.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# make_intel_card DRM_ROOT CARD DEVICE_ID LMEM_MB [NAME]
make_intel_card() {
    local dev="$1/$2/device"
    mkdir -p "$dev"
    printf '0x8086\n' > "$dev/vendor"
    printf '%s\n' "$3" > "$dev/device"
    [[ "$4" -gt 0 ]] && printf '%s\n' "$(( $4 * 1048576 ))" > "$dev/lmem_total_bytes"
    [[ -z "${5:-}" ]] || printf '%s\n' "$5" > "$dev/product_name"
}

detect() (
    export ODS_DRM_SYS="$1"
    log() { :; }; warn() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_bad() { :; }
    lspci() { return 1; }
    nvidia-smi() { return 1; }
    SCRIPT_DIR="$ROOT"
    # shellcheck source=../installers/lib/detection.sh
    . "$ROOT/installers/lib/detection.sh"
    detect_gpu >/dev/null
    printf '%s|%s|%s|%s|%s|%s\n' "$GPU_BACKEND" "$GPU_COUNT" "$GPU_NAME" "$GPU_VRAM" "$GPU_MEMORY_TYPE" "$GPU_DEVICE_ID"
)

# Dual Battlemage G31 (0xe223, 24 GB each): the lspci name contains no "Arc".
bmg_duo="$tmp/bmg-duo/drm"
make_intel_card "$bmg_duo" card0 0xe223 24576 "Arc Pro B60"
make_intel_card "$bmg_duo" card1 0xe223 24576 "Arc Pro B60"

got="$(detect "$bmg_duo")"
[[ "$got" == "intel|2|Arc Pro B60 × 2|49152|discrete|0xe223" ]] \
    || fail "dual Battlemage must detect as 2x Intel Arc with summed VRAM, got: $got"
pass "Battlemage 0xe223 detected as Intel Arc, multi-GPU VRAM summed"

# Single Alchemist A770 (0x56a0, 16 GB).
alc="$tmp/alchemist/drm"
make_intel_card "$alc" card0 0x56a0 16384 "Intel Arc A770"

got="$(detect "$alc")"
[[ "$got" == "intel|1|Intel Arc A770|16384|discrete|0x56a0" ]] \
    || fail "Alchemist A770 must still detect, got: $got"
pass "Alchemist 0x56a0 unchanged"

# Battlemage without product_name and without lspci: ID-only naming.
bmg_noname="$tmp/bmg-noname/drm"
make_intel_card "$bmg_noname" card0 0xe20b 12288

got="$(detect "$bmg_noname")"
[[ "$got" == "intel|1|Intel Arc (0xe20b)|12288|discrete|0xe20b" ]] \
    || fail "unnamed Battlemage must fall back to a device-ID name, got: $got"
pass "no lspci / no product_name still detects"

# Battlemage on the xe driver: no lmem_total_bytes, VRAM comes from the
# largest PCI BAR aperture (32 GiB aperture on the real BMG-G31 below).
bmg_xe="$tmp/bmg-xe/drm"
make_intel_card "$bmg_xe" card0 0xe223 0
dev="$bmg_xe/card0/device"
printf '0x0000002800000000 0x0000002800ffffff 0x000000000014220c\n' > "$dev/resource"
printf '0x0000001800000000 0x0000001fffffffff 0x000000000014220c\n' >> "$dev/resource"
printf '0x00000000fc200000 0x00000000fc3fffff 0x0000000000046200\n' >> "$dev/resource"

got="$(detect "$bmg_xe")"
[[ "$got" == "intel|1|Intel Arc (0xe223)|32768|discrete|0xe223" ]] \
    || fail "xe card without lmem must read VRAM from the largest BAR, got: $got"
pass "xe fallback reads VRAM from the PCI BAR aperture"

# Battlemage with no VRAM evidence at all: still detects.
bmg_novram="$tmp/bmg-novram/drm"
make_intel_card "$bmg_novram" card0 0xe20b 0

got="$(detect "$bmg_novram")"
[[ "$got" == intel\|1\|*0\|discrete\|0xe20b ]] \
    || fail "missing lmem_total_bytes must not hide the GPU, got: $got"
pass "missing lmem_total_bytes still detects the card"

# Integrated Intel GPUs must never become the inference backend.
igpu="$tmp/igpu/drm"
make_intel_card "$igpu" card0 0x7d55 0   # Meteor Lake Arc iGPU
make_intel_card "$igpu" card1 0xa7a0 0   # Raptor Lake UHD iGPU

got="$(detect "$igpu")"
[[ "$got" == cpu\|* ]] \
    || fail "integrated Xe iGPUs must not be detected as Arc, got: $got"
pass "integrated iGPUs ignored"

echo "All Intel Arc detection tests passed."
