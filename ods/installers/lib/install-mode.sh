#!/usr/bin/env bash
# Preserve an existing explicit ODS runtime mode across installer reruns.

ods_existing_install_mode() {
    local env_file="$1" expected_uid="${2:-${UID:-$(id -u)}}"
    local owner_uid file_mode file_size line value="" found=false

    [[ -f "$env_file" && ! -L "$env_file" ]] || return 1
    read -r owner_uid file_mode file_size < <(
        stat -c '%u %a %s' -- "$env_file" 2>/dev/null
    ) || return 1
    [[ "$owner_uid" == "$expected_uid" ]] || return 1
    [[ "$file_mode" =~ ^[0-7]{3,4}$ && "$file_size" =~ ^[0-9]+$ ]] || return 1
    (( (8#$file_mode & 8#022) == 0 )) || return 1
    (( file_size > 0 && file_size <= 1048576 )) || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == ODS_MODE=* ]] || continue
        [[ "$found" == "false" ]] || return 1
        found=true
        value="${line#ODS_MODE=}"
        case "$value" in
            local|cloud|hybrid|lemonade) ;;
            *) return 1 ;;
        esac
    done <"$env_file"

    [[ "$found" == "true" ]] || return 1
    printf '%s\n' "$value"
}

ods_preserve_existing_install_mode() {
    local current_mode="$1" mode_explicit="$2" env_file="$3" existing_mode

    if [[ "$mode_explicit" == "true" ]]; then
        printf '%s\n' "$current_mode"
        return 0
    fi
    if existing_mode="$(ods_existing_install_mode "$env_file")"; then
        printf '%s\n' "$existing_mode"
    else
        printf '%s\n' "$current_mode"
    fi
}
