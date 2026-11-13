#!/bin/bash
# Bound the install filesystem's virtual free space by the Windows drive
# holding its WSL disk. Windows setup passes that drive before .env exists.
# Requires ods_is_wsl_host(), log(), warn(), and the current DISK_AVAIL.
ods_bound_wsl_disk_avail() {
    local probe="$1" drive="${ODS_WSL_DISK_DRIVE:-}" root_dev probe_dev mount mount_dev free_gb
    ods_is_wsl_host || return 0
    root_dev="$(stat -Lc '%d' / 2>/dev/null)" || return 0
    probe_dev="$(stat -Lc '%d' "$probe" 2>/dev/null)" || return 0
    # A separate mounted filesystem has its own capacity; it does not grow
    # this distribution's virtual disk. Follow aliases just as df does.
    [[ "$probe_dev" == "$root_dev" ]] || return 0
    if [[ ! "$drive" =~ ^[A-Za-z]:$ ]]; then
        warn "Windows backing-drive free space could not be checked: rerun Windows setup to record the WSL disk drive."
        return 0
    fi
    mount="$(wslpath -u "${drive}\\" 2>/dev/null)" || mount=""
    mount_dev=""
    [[ -z "$mount" ]] || mount_dev="$(stat -Lc '%d' "$mount" 2>/dev/null)" || mount_dev=""
    if [[ -z "$mount_dev" || "$mount_dev" == "$root_dev" ]]; then
        warn "Windows backing-drive free space could not be checked: $drive is not mounted in WSL."
        return 0
    fi
    free_gb="$(df -Pk "$mount" 2>/dev/null | awk 'END {if ($4 ~ /^[0-9]+$/) printf "%.0f", int($4 / 1048576)}')" || free_gb=""
    if [[ ! "$free_gb" =~ ^[0-9]+$ ]]; then
        warn "Windows backing-drive free space could not be read for $drive ($mount)."
        return 0
    fi
    log "Windows drive $drive under WSL: ${free_gb}GB free ($mount); virtual filesystem: ${DISK_AVAIL}GB free"
    if (( free_gb < DISK_AVAIL )); then
        DISK_AVAIL="$free_gb"
    fi
}
