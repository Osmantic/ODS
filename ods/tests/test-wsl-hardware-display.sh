#!/usr/bin/env bash
# The installer's hardware summary must not read as a detection error on
# Windows (#7311): VRAM reports rounded GiB and exact MiB (a 24GiB card reports
# about 24564MiB), and the WSL RAM limit is distinguished from host memory. The
# Linux-only NVIDIA Blackwell module check is skipped under WSL, where the
# Windows driver serves the GPU.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $1"; exit 1; }

extract() {
    awk -v signature="$1() {" '$0 == signature { f = 1 } f { print } f && $0 == "}" { exit }' "$2"
}

summary="$(extract show_hardware_summary "$ROOT/installers/lib/ui.sh")"
[[ -n "$summary" ]] || fail "show_hardware_summary was not found"
eval "$summary"
GRN="" NC="" BGRN=""
source "$ROOT/installers/lib/wsl-memory.sh"

out="$(show_hardware_summary "NVIDIA RTX 3090 Ti" "$(ods_format_vram_mib 24564)" \
    "Ryzen 9" "46.9 GiB" "500" "96.0 GiB" true)"
grep -qE 'WSL RAM: +46\.9 GiB' <<< "$out" || fail "the WSL RAM limit is missing: $out"
grep -qE 'Windows RAM: +96\.0 GiB' <<< "$out" || fail "Windows host RAM is missing: $out"
grep -qF '24.0 GiB (24564 MiB)' <<< "$out" || fail "rounded VRAM must preserve exact MiB: $out"
out="$(show_hardware_summary "NVIDIA RTX 3090 Ti" "$(ods_format_vram_mib 24564)" \
    "Ryzen 9" "64.0 GiB" "500")"
grep -qE 'RAM: +64\.0 GiB +\|' <<< "$out" || fail "native RAM changed: $out"

grep -qF 'show_hardware_summary "$GPU_NAME" "$(ods_format_vram_mib "$GPU_VRAM")"' \
    "$ROOT/installers/phases/02-detection.sh" \
    || fail "the GPU summary must use the rounded display without changing exact VRAM"
grep -qF '"$DISK_AVAIL" "$_host_ram_display" "$RAM_IS_WSL"' "$ROOT/installers/phases/02-detection.sh" \
    || fail "the hardware summary must receive Windows RAM and the WSL boundary"

check="$(extract validate_nvidia_blackwell_open_modules "$ROOT/installers/lib/detection.sh")"
[[ -n "$check" ]] || fail "validate_nvidia_blackwell_open_modules was not found"
eval "$check"

ods_is_wsl_host() { return 0; }
nvidia_blackwell_hardware_detected() { fail "the Blackwell module probe ran under WSL"; }
validate_nvidia_blackwell_open_modules || fail "the Blackwell check failed under WSL"

ods_is_wsl_host() { return 1; }
probed=false
nvidia_blackwell_hardware_detected() { probed=true; return 1; }
validate_nvidia_blackwell_open_modules
[[ "$probed" == true ]] || fail "the Blackwell check no longer runs on native Linux"

echo "PASS: WSL hardware summary names the RAM limit, rounds VRAM, and skips the Linux Blackwell check"
