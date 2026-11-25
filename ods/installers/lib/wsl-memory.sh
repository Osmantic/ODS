#!/bin/bash
# Runtime/Windows host memory reporting and WSL model-memory policy helpers.

ODS_WSL_CONTROL_PLANE_HEADROOM_GB_DEFAULT=2

# Windows interop is optional in WSL. Its executable can be on PATH while the
# WSLInterop binfmt handler is unavailable (Exec format error, exit 126).
# Never make an optional host-RAM lookup abort Linux hardware detection.
ods_wsl_host_ram_kb() {
    local host_bytes="" host_kb=""

    if command -v powershell.exe >/dev/null 2>&1; then
        host_bytes="$(powershell.exe -NoProfile -Command \
            '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory' 2>/dev/null \
            | tr -d '\r')" || host_bytes=""
        if [[ "$host_bytes" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$((host_bytes / 1024))"
            return 0
        fi
    fi

    if command -v wmic.exe >/dev/null 2>&1; then
        host_kb="$(wmic.exe OS get TotalVisibleMemorySize /value 2>/dev/null \
            | grep -oE '[0-9]+' | sed -n '1p')" || host_kb=""
        if [[ "$host_kb" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$host_kb"
            return 0
        fi
    fi

    return 1
}

ods_wsl_model_ram_budget() {
    local vm_ram_gb="${1:-0}"
    local headroom_gb="${2:-${ODS_WSL_CONTROL_PLANE_HEADROOM_GB:-$ODS_WSL_CONTROL_PLANE_HEADROOM_GB_DEFAULT}}"

    [[ "$vm_ram_gb" =~ ^[0-9]+$ ]] || return 2
    if [[ ! "$headroom_gb" =~ ^[0-9]+$ ]]; then
        headroom_gb="$ODS_WSL_CONTROL_PLANE_HEADROOM_GB_DEFAULT"
    fi

    if (( vm_ram_gb <= headroom_gb )); then
        printf '0\n'
    else
        printf '%s\n' "$((vm_ram_gb - headroom_gb))"
    fi
}

# Runtime contracts always use Linux MemTotal, including cloud installs. A
# Windows host lookup is presentation-only: it must never increase model or
# Compose limits beyond the memory addressable by this WSL VM.
ods_detect_runtime_ram() {
    RAM_KB="$(awk '$1 == "MemTotal:" { print $2; exit }' \
        "${ODS_PROC_MEMINFO_FILE:-/proc/meminfo}")" || return 1
    [[ "$RAM_KB" =~ ^[0-9]+$ && "$RAM_KB" -gt 0 ]] || return 1
    RAM_GB=$((RAM_KB / 1024 / 1024))
    MODEL_TIER_RAM_GB="$RAM_GB"
    WINDOWS_HOST_RAM_KB=""
    RAM_IS_WSL=false

    if declare -F ods_is_wsl_host >/dev/null 2>&1; then
        ods_is_wsl_host && RAM_IS_WSL=true
    elif grep -qiE 'microsoft|wsl' "${ODS_PROC_VERSION_FILE:-/proc/version}" 2>/dev/null; then
        RAM_IS_WSL=true
    fi
    if [[ "$RAM_IS_WSL" == true ]]; then
        WINDOWS_HOST_RAM_KB="$(ods_wsl_host_ram_kb)" || WINDOWS_HOST_RAM_KB=""
        MODEL_TIER_RAM_GB="$(ods_wsl_model_ram_budget "$RAM_GB")"
    fi
    return 0
}

# Round only for display. Internal RAM remains floored GiB and GPU memory
# remains the exact MiB returned by the driver, preserving conservative sizing.
ods_format_memory_kib() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 2
    LC_ALL=C awk -v kib="$1" 'BEGIN { printf "%.1f GiB", kib / 1048576 }'
}

ods_format_vram_mib() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 2
    LC_ALL=C awk -v mib="$1" 'BEGIN { printf "%.1f GiB (%s MiB)", mib / 1024, mib }'
}
