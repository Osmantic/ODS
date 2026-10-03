#!/bin/bash
# WSL model-memory policy helpers.

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
