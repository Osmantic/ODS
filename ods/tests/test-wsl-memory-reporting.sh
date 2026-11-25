#!/usr/bin/env bash
# Hardware presentation must distinguish the Windows host from the WSL budget,
# and rounded display values must never increase model or service budgets.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

printf 'MemTotal:       49209324 kB\n' > "$tmp/meminfo"
export ODS_PROC_MEMINFO_FILE="$tmp/meminfo"
ODS_TEST_WSL=true
ODS_TEST_HOST_AVAILABLE=true
ods_is_wsl_host() { [[ "$ODS_TEST_WSL" == true ]]; }
powershell.exe() {
    [[ "$ODS_TEST_HOST_AVAILABLE" == true ]] || return 126
    printf '103079215104\r\n' # 96 GiB Windows host
}
wmic.exe() { return 126; }

source "$root/installers/lib/wsl-memory.sh"
ods_detect_runtime_ram
[[ "$RAM_IS_WSL" == true ]]
[[ "$WINDOWS_HOST_RAM_KB" == 100663296 ]]
[[ "$RAM_KB" == 49209324 && "$RAM_GB" == 46 ]]
[[ "$MODEL_TIER_RAM_GB" == 44 ]]
[[ "$(ods_format_memory_kib "$RAM_KB")" == '46.9 GiB' ]]
[[ "$(ods_format_memory_kib "$WINDOWS_HOST_RAM_KB")" == '96.0 GiB' ]]
[[ "$(ods_format_vram_mib 24564)" == '24.0 GiB (24564 MiB)' ]]
[[ "$(ods_format_vram_mib 8151)" == '8.0 GiB (8151 MiB)' ]]

# A stale host result must not survive unavailable Windows interop or a later
# native Linux probe. In either case MemTotal remains the runtime contract.
ODS_TEST_HOST_AVAILABLE=false
ods_detect_runtime_ram
[[ -z "$WINDOWS_HOST_RAM_KB" ]]
[[ "$RAM_GB" == 46 && "$MODEL_TIER_RAM_GB" == 44 ]]
ODS_TEST_WSL=false
ods_detect_runtime_ram
[[ "$RAM_IS_WSL" == false && -z "$WINDOWS_HOST_RAM_KB" ]]
[[ "$RAM_GB" == 46 && "$MODEL_TIER_RAM_GB" == 46 ]]

# Execute the cloud branch, which used to replace WSL MemTotal with host RAM.
(
    ODS_TEST_WSL=true
    ODS_TEST_HOST_AVAILABLE=true
    SCRIPT_DIR="$root"
    ODS_MODE=cloud
    INTERACTIVE=false
    ods_progress() { :; }
    chapter() { :; }
    ai() { :; }
    log() { :; }
    error() { printf '%s\n' "$*" >&2; exit 1; }
    resolve_compose_config() { :; }
    resolve_tier_config() { :; }
    source "$root/installers/phases/02-detection.sh"
    [[ "$RAM_GB" == 46 && "$MODEL_TIER_RAM_GB" == 44 ]]
    [[ "$WINDOWS_HOST_RAM_KB" == 100663296 ]]
)

# Inspect the actual summary that users see, including the exact NVIDIA MiB
# value behind its rounded GiB presentation.
GRN='' BGRN='' NC=''
source "$root/installers/lib/ui.sh"
summary="$(show_hardware_summary 'NVIDIA GeForce RTX 3090 Ti' \
    "$(ods_format_vram_mib 24564)" 'AMD Ryzen 9 9950X3D' \
    '46.9 GiB' 100 '96.0 GiB' true)"
grep -Fq '24.0 GiB (24564 MiB)' <<< "$summary"
grep -Eq 'Windows RAM:[[:space:]]+96\.0 GiB' <<< "$summary"
grep -Eq 'WSL RAM:[[:space:]]+46\.9 GiB' <<< "$summary"

summary="$(show_hardware_summary 'NVIDIA GPU' '8.0 GiB (8151 MiB)' \
    'CPU' '46.9 GiB' 100 '' true)"
grep -Fq 'Windows RAM:' <<< "$summary"
grep -Fq 'Unavailable (Windows interop)' <<< "$summary"
summary="$(show_hardware_summary 'NVIDIA GPU' '8.0 GiB (8151 MiB)' \
    'CPU' '46.9 GiB' 100)"
! grep -Fq 'Windows RAM:' <<< "$summary"
! grep -Fq 'WSL RAM:' <<< "$summary"

printf 'PASS: hardware memory reporting distinguishes WSL and host without inflating budgets\n'
