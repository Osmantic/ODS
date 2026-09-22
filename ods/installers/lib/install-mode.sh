#!/usr/bin/env bash
# Preserve an existing explicit ODS runtime mode across installer reruns.

ods_existing_install_mode() {
    local env_file="$1" expected_uid="${2:-${UID:-$(id -u)}}"
    local owner_uid file_mode file_size line value="" found=false
    local modes='local|cloud|hybrid|lemonade' value_pattern
    # Accept literal dotenv formatting without evaluating any shell content.
    value_pattern="^[[:space:]]*(($modes)|\"($modes)\"|'($modes)')([[:space:]]+#.*)?[[:space:]]*$"

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
        [[ "$line" =~ ^[[:space:]]*ODS_MODE[[:space:]]*= ]] || continue
        [[ "$found" == "false" ]] || return 1
        found=true
        [[ "${line#*=}" =~ $value_pattern ]] || return 1
        value="${BASH_REMATCH[2]}${BASH_REMATCH[3]}${BASH_REMATCH[4]}"
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

# Read the recorded enablement of an optional service from an installed tree.
# Phase 03 (_sync_extension_compose_at) renames each optional service's
# compose.yaml to compose.yaml.disabled when its feature is off, so the
# installed marker pair is the authoritative record of the last selection —
# the same record resolve-compose-stack.sh consumes when it skips *.disabled
# files. Prints "true" or "false"; returns 1 when there is no record (fresh
# install) or when both markers exist (interrupted sync — ambiguous).
ods_existing_feature_enabled() {
    local svc_dir="$1" install_root="$2"
    local compose="$install_root/extensions/services/$svc_dir/compose.yaml"

    if [[ -f "$compose" && ! -e "${compose}.disabled" ]]; then
        printf 'true\n'
        return 0
    fi
    if [[ -f "${compose}.disabled" && ! -e "$compose" ]]; then
        printf 'false\n'
        return 0
    fi
    return 1
}

# Keep a feature flag at its caller value when it was explicitly set this run;
# when it was not, restore the selection recorded by the previous install.
# Unknown or ambiguous records keep the caller value (install-core defaults).
ods_preserve_feature_flag() {
    local current="$1" explicit="$2" svc_dir="$3" install_root="$4" existing

    if [[ "$explicit" == "true" ]]; then
        printf '%s\n' "$current"
        return 0
    fi
    if existing="$(ods_existing_feature_enabled "$svc_dir" "$install_root")"; then
        printf '%s\n' "$existing"
    else
        printf '%s\n' "$current"
    fi
}
