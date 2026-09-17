#!/bin/bash
# WSL model-memory policy helpers.

ODS_WSL_CONTROL_PLANE_HEADROOM_GB_DEFAULT=2

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
