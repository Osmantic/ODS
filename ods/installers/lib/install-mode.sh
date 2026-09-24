#!/usr/bin/env bash
# Preserve an existing explicit ODS runtime mode across installer reruns.

# Reads a single literal key from an existing installation's .env without
# evaluating any shell content. Fails closed: the file must be a regular,
# owner-only file owned by the expected uid, contain exactly one entry for the
# key, and the value must match `states` verbatim (optionally quoted).
_ods_existing_env_literal() {
    local env_file="$1" key="$2" states="$3" expected_uid="${4:-${UID:-$(id -u)}}"
    local owner_uid file_mode file_size line value="" found=false
    # Accept literal dotenv formatting without evaluating any shell content.
    local value_pattern="^[[:space:]]*(($states)|\"($states)\"|'($states)')([[:space:]]+#.*)?[[:space:]]*$"

    [[ -f "$env_file" && ! -L "$env_file" ]] || return 1
    read -r owner_uid file_mode file_size < <(
        stat -c '%u %a %s' -- "$env_file" 2>/dev/null
    ) || return 1
    [[ "$owner_uid" == "$expected_uid" ]] || return 1
    [[ "$file_mode" =~ ^[0-7]{3,4}$ && "$file_size" =~ ^[0-9]+$ ]] || return 1
    (( (8#$file_mode & 8#022) == 0 )) || return 1
    (( file_size > 0 && file_size <= 1048576 )) || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*= ]] || continue
        [[ "$found" == "false" ]] || return 1
        found=true
        [[ "${line#*=}" =~ $value_pattern ]] || return 1
        value="${BASH_REMATCH[2]}${BASH_REMATCH[3]}${BASH_REMATCH[4]}"
    done <"$env_file"

    [[ "$found" == "true" ]] || return 1
    printf '%s\n' "$value"
}

ods_existing_install_mode() {
    _ods_existing_env_literal "$1" 'ODS_MODE' 'local|cloud|hybrid|lemonade' "${2:-}"
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

# Same persisted-marker rules for LEMONADE_EXTERNAL: only the literals
# true/false are accepted, anything else fails closed.
ods_existing_lemonade_external() {
    _ods_existing_env_literal "$1" 'LEMONADE_EXTERNAL' 'true|false' "${2:-}"
}

# An explicit environment value or a flag-chosen selection always wins over
# the persisted marker; only an untouched rerun inherits it.
ods_preserve_lemonade_external() {
    local current="$1" explicit="$2" env_file="$3" existing

    if [[ "$explicit" == "true" || "${current,,}" == "true" ]]; then
        printf '%s\n' "$current"
        return 0
    fi
    if existing="$(ods_existing_lemonade_external "$env_file")"; then
        printf '%s\n' "$existing"
    else
        printf '%s\n' "$current"
    fi
}
