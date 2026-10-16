#!/usr/bin/env bash
# An AMD integrated GPU next to a discrete AMD GPU (a desktop Ryzen's 2-CU
# Radeon plus a Radeon card) must not become a second inference GPU or the
# one llama.cpp runs on. The installer counts only the discrete GPU, so the
# install takes the single-GPU overlay, where llama.cpp's default device list
# (discrete GPUs; integrated ones only when there is none, llama.cpp b9014
# src/llama.cpp) picks the discrete GPU whatever the Vulkan order. An APU on
# its own (Strix Halo) keeps working unchanged.
#
# Fixtures are mock /sys/class/drm trees. gpu_metrics holds only the 4-byte
# table header: amdgpu uses format revision 1 for discrete GPUs and 2 or 3
# for APUs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# make_card ROOT CARD DEVICE_ID VRAM_MB GTT_MB METRICS_FORMAT GC_MAJOR GC_MINOR GC_REV [NAME]
# METRICS_FORMAT "none" leaves gpu_metrics out (an older kernel or SMU).
make_card() {
    local drm="$1" card="$2" device_id="$3" vram_mb="$4" gtt_mb="$5" format="$6"
    local major="$7" minor="$8" rev="$9" name="${10:-}"
    local dev="$drm/$card/device"
    mkdir -p "$dev/ip_discovery/die/0/GC/0"
    printf '0x1002\n' > "$dev/vendor"
    printf '%s\n' "$device_id" > "$dev/device"
    printf '%s\n' "$(( vram_mb * 1048576 ))" > "$dev/mem_info_vram_total"
    printf '%s\n' "$(( gtt_mb * 1048576 ))" > "$dev/mem_info_gtt_total"
    printf '%s\n' "$major" > "$dev/ip_discovery/die/0/GC/0/major"
    printf '%s\n' "$minor" > "$dev/ip_discovery/die/0/GC/0/minor"
    printf '%s\n' "$rev" > "$dev/ip_discovery/die/0/GC/0/revision"
    [[ -z "$name" ]] || printf '%s\n' "$name" > "$dev/product_name"
    case "$format" in
        1) printf '\x58\x00\x01\x03' > "$dev/gpu_metrics" ;;
        2) printf '\x58\x00\x02\x01' > "$dev/gpu_metrics" ;;
        3) printf '\x58\x00\x03\x00' > "$dev/gpu_metrics" ;;
        none) ;;
        *) fail "unknown metrics format $format" ;;
    esac
}

# Raphael iGPU (gfx1036, 512 MB carve-out) on card0, RX 7900 XTX (gfx1100) on
# card1, on a 64 GB host (31 GB GTT each): the order a board with the monitor
# on the motherboard shows.
pair="$tmp/pair/drm"
make_card "$pair" card0 0x164e 512 31744 2 10 3 6
make_card "$pair" card1 0x744c 24576 31744 1 11 0 0 "AMD Radeon RX 7900 XTX"

# Strix Halo: the APU alone (gfx1151, 96 GB GTT).
halo="$tmp/halo/drm"
make_card "$halo" card0 0x1586 512 98304 3 11 5 1 "AMD Radeon 8060S"

# iGPU between two discrete cards, for the multi-GPU topology.
trio="$tmp/trio/drm"
make_card "$trio" card0 0x744c 24576 31744 1 11 0 0 "AMD Radeon RX 7900 XTX"
make_card "$trio" card1 0x164e 512 31744 2 10 3 6
make_card "$trio" card2 0x7480 16384 31744 1 11 0 1 "AMD Radeon RX 7800 XT"

# The same pair on a kernel without gpu_metrics: no evidence, so nothing is
# left out (the earlier behaviour).
unknown="$tmp/unknown/drm"
make_card "$unknown" card0 0x164e 512 31744 none 10 3 6
make_card "$unknown" card1 0x744c 24576 31744 none 11 0 0 "AMD Radeon RX 7900 XTX"

detect() (
    export ODS_DRM_SYS="$1"
    log() { :; }; warn() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_bad() { :; }
    lspci() { :; }
    nvidia-smi() { return 1; }
    SCRIPT_DIR="$ROOT"
    # shellcheck source=../installers/lib/detection.sh
    . "$ROOT/installers/lib/detection.sh"
    detect_gpu >/dev/null
    printf '%s|%s|%s|%s|%s|%s\n' "$GPU_BACKEND" "$GPU_COUNT" "$GPU_NAME" "$GPU_VRAM" "$GPU_MEMORY_TYPE" "$GPU_DEVICE_ID"
)

gfx_targets() (
    export ODS_DRM_SYS="$1"
    log() { :; }; warn() { :; }
    # shellcheck source=../installers/lib/amd-topo.sh
    . "$ROOT/installers/lib/amd-topo.sh"
    amd_gfx_targets | paste -sd, -
)

got="$(detect "$pair")"
[[ "$got" == "amd|1|AMD Radeon RX 7900 XTX|24576|discrete|0x744c" ]] \
    || fail "iGPU + discrete GPU must detect one discrete inference GPU, got: $got"
got="$(gfx_targets "$pair")"
[[ "$got" == "gfx1100" ]] \
    || fail "the backend choice and HSA override must see only the discrete target, got: $got"
pass "an integrated GPU next to a discrete AMD GPU is not counted for inference"

# A discrete GPU on a 128 GB host has 62 GB of GTT, which the size heuristic
# alone took for an APU's unified pool; gpu_metrics v1 settles it.
bigram="$tmp/bigram/drm"
make_card "$bigram" card0 0x744c 24576 63488 1 11 0 0 "AMD Radeon RX 7900 XTX"
got="$(detect "$bigram")"
[[ "$got" == "amd|1|AMD Radeon RX 7900 XTX|24576|discrete|0x744c" ]] \
    || fail "a discrete GPU on a big-RAM host must stay discrete, got: $got"
pass "gpu_metrics keeps a discrete GPU discrete whatever the GTT size"

got="$(detect "$halo")"
[[ "$got" == "amd|1|AMD Radeon 8060S|512|unified|0x1586" ]] \
    || fail "Strix Halo must keep its APU as the inference GPU, got: $got"
got="$(gfx_targets "$halo")"
[[ "$got" == "gfx1151" ]] || fail "Strix Halo must keep its gfx1151 target, got: $got"
pass "Strix Halo (APU alone) is unchanged"

got="$(detect "$trio")"
expected="amd|2|AMD Radeon RX 7900 XTX + AMD Radeon RX 7800 XT|40960|discrete|0x744c"
[[ "$got" == "$expected" ]] \
    || fail "two discrete GPUs around an iGPU must count as two, got: $got"
if command -v jq >/dev/null 2>&1; then
    topo="$(
        export ODS_DRM_SYS="$trio"
        log() { :; }; warn() { :; }
        . "$ROOT/installers/lib/amd-topo.sh"
        detect_amd_topo 2>/dev/null
    )"
    got="$(jq -r '[.gpu_count, ([.gpus[] | "\(.index):\(.gfx_version)"] | join(","))] | join("|")' <<<"$topo")"
    [[ "$got" == "2|0:gfx1100,1:gfx1101" ]] \
        || fail "the multi-GPU topology must hold the discrete GPUs only, indexed in DRM order, got: $got"
    pass "multi-GPU topology leaves the iGPU out and keeps DRM order"
else
    printf '[SKIP] jq unavailable; multi-GPU topology case left to CI\n'
fi

got="$(detect "$unknown")"
[[ "$got" == amd\|2\|* ]] \
    || fail "without gpu_metrics nothing is left out (no evidence of an iGPU), got: $got"
pass "without gpu_metrics both GPUs stay counted (earlier behaviour)"

# The single-GPU overlay pins no device: llama.cpp's own discrete-first
# default does the choosing. The multi-GPU overlay turns off Mesa's
# device-select layer so GGML_VK_VISIBLE_DEVICES indices follow PCI order.
single="$ROOT/docker-compose.amd.yml"
grep -qF 'LLAMA_ARG_SPLIT_MODE=${LLAMA_ARG_SPLIT_MODE:-none}' "$single" \
    || fail "the single-GPU AMD overlay must default to split-mode none"
if grep -vE '^[[:space:]]*#' "$single" | grep -qE 'LLAMA_ARG_DEVICE|GGML_VK_VISIBLE_DEVICES|--device|MESA_VK_DEVICE_SELECT|main-gpu|LLAMA_ARG_MAIN_GPU'; then
    fail "the single-GPU AMD overlay must leave the device choice to llama.cpp's discrete-first default"
fi
grep -qE '^[[:space:]]+NODEVICE_SELECT: "1"$' "$ROOT/docker-compose.multigpu-amd.yml" \
    || fail "the AMD multi-GPU overlay must turn off Mesa's device-select reordering"
pass "AMD overlays leave the single-GPU choice to llama.cpp and keep multi-GPU indices in PCI order"
