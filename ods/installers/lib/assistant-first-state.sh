#!/bin/bash
# Assistant First host-state directory custody.
#
# This helper is intentionally Linux-specific. The public-beta profile is
# qualified only where GNU stat and the host-UID Compose contract are present.

_ods_assistant_first_directory_metadata() {
    LC_ALL=C stat -c '%u:%a' -- "$1" 2>/dev/null || true
}

_ods_assistant_first_data_mode_is_safe() {
    local mode="$1" numeric

    [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    numeric=$((8#${mode}))
    # The owner must be able to use the tree, no other account may write it,
    # and special directory semantics are not part of this custody contract.
    (( (numeric & 0700) == 0700 \
        && (numeric & 0022) == 0 \
        && (numeric & 07000) == 0 ))
}

_ods_assistant_first_require_real_directory() {
    local target="$1" description="$2"

    if [[ -L "$target" ]]; then
        error "$description must not be a symlink: $target"
        return 1
    fi
    if [[ ! -d "$target" ]]; then
        error "$description is not a directory: $target"
        return 1
    fi
}

_ods_assistant_first_preflight_existing_directory() {
    local target="$1" description="$2"

    if [[ -L "$target" ]]; then
        error "$description must not be a symlink: $target"
        return 1
    fi
    if [[ -e "$target" && ! -d "$target" ]]; then
        error "$description path is not a directory: $target"
        return 1
    fi
}

_ods_assistant_first_prepare_shared_directory() {
    local target="$1" description="$2" expected_uid="$3"
    local metadata owner mode

    _ods_assistant_first_preflight_existing_directory "$target" "$description" \
        || return 1
    if [[ ! -e "$target" ]]; then
        if ! (umask 022 && mkdir -- "$target"); then
            _ods_assistant_first_require_real_directory "$target" "$description" \
                || return 1
        fi
    fi
    _ods_assistant_first_require_real_directory "$target" "$description" \
        || return 1

    metadata="$(_ods_assistant_first_directory_metadata "$target")"
    owner="${metadata%%:*}"
    mode="${metadata#*:}"
    if [[ "$owner" != "$expected_uid" ]]; then
        error "$description is not owned by the installing host UID: $target"
        return 1
    fi
    if ! _ods_assistant_first_data_mode_is_safe "$mode"; then
        _ods_assistant_first_require_real_directory "$target" "$description" \
            || return 1
        chmod 0755 -- "$target" || {
            error "Could not normalize $description mode."
            return 1
        }
        _ods_assistant_first_require_real_directory "$target" "$description" \
            || return 1
        metadata="$(_ods_assistant_first_directory_metadata "$target")"
        owner="${metadata%%:*}"
        mode="${metadata#*:}"
        if [[ "$owner" != "$expected_uid" ]] \
            || ! _ods_assistant_first_data_mode_is_safe "$mode"; then
            error "$description custody verification failed."
            return 1
        fi
    fi
}

_ods_assistant_first_prepare_private_directory() {
    local target="$1" description="$2" expected_uid="$3"
    local metadata owner

    _ods_assistant_first_preflight_existing_directory "$target" "$description" \
        || return 1
    if [[ ! -e "$target" ]]; then
        if ! (umask 077 && mkdir -- "$target"); then
            # A concurrent same-owner installer may have created the directory.
            _ods_assistant_first_require_real_directory "$target" "$description" \
                || return 1
        fi
    fi
    _ods_assistant_first_require_real_directory "$target" "$description" \
        || return 1

    metadata="$(_ods_assistant_first_directory_metadata "$target")"
    owner="${metadata%%:*}"
    if [[ "$owner" != "$expected_uid" ]]; then
        error "$description is not owned by the installing host UID: $target"
        return 1
    fi
    if [[ "$metadata" != "$expected_uid:700" ]]; then
        # Recheck before chmod. The verified parent is not group/world-writable,
        # so only the trusted installing UID can replace the final component.
        _ods_assistant_first_require_real_directory "$target" "$description" \
            || return 1
        chmod 700 -- "$target" || {
            error "Could not make $description owner-private."
            return 1
        }
        _ods_assistant_first_require_real_directory "$target" "$description" \
            || return 1
        metadata="$(_ods_assistant_first_directory_metadata "$target")"
        if [[ "$metadata" != "$expected_uid:700" ]]; then
            error "$description custody verification failed."
            return 1
        fi
    fi
}

ods_assistant_first_prepare_state_directories() {
    local data_root="$1" expected_uid="$2"
    local child_name child_path

    case "$expected_uid" in
        ''|*[!0-9]*)
            error "Assistant First state custody requires a numeric host UID."
            return 1
            ;;
    esac
    if [[ -z "$data_root" ]]; then
        error "Assistant First state custody requires a data directory."
        return 1
    fi

    # This must run before any child mkdir. In particular, never follow a
    # pre-existing or broken data symlink into an unrelated tree.
    _ods_assistant_first_prepare_shared_directory \
        "$data_root" "Assistant First data directory" "$expected_uid" \
        || return 1

    # Preflight every child before creating any of them. This prevents a stale
    # unsafe data root from turning a later rejection into a partial bootstrap.
    for child_name in assistant-first .extension-operation-locks config models persona; do
        child_path="$data_root/$child_name"
        _ods_assistant_first_preflight_existing_directory \
            "$child_path" "Assistant First data child" || return 1
    done
    _ods_assistant_first_preflight_existing_directory \
        "$data_root/assistant-first/artifact-stage" \
        "Assistant First artifact stage directory" || return 1
    _ods_assistant_first_preflight_existing_directory \
        "$data_root/assistant-first/resource-reservations" \
        "Assistant First resource reservation directory" || return 1

    _ods_assistant_first_prepare_private_directory \
        "$data_root/assistant-first" \
        "Assistant First private state directory" \
        "$expected_uid" || return 1
    _ods_assistant_first_prepare_private_directory \
        "$data_root/assistant-first/artifact-stage" \
        "Assistant First artifact stage directory" \
        "$expected_uid" || return 1
    _ods_assistant_first_prepare_private_directory \
        "$data_root/assistant-first/resource-reservations" \
        "Assistant First resource reservation directory" \
        "$expected_uid" || return 1
    _ods_assistant_first_prepare_private_directory \
        "$data_root/.extension-operation-locks" \
        "Assistant First mutation lock directory" \
        "$expected_uid" || return 1

    # These shared roots are also checked rather than blindly reached through
    # mkdir -p. A previously unsafe data root may already contain hostile links.
    for child_name in config models persona; do
        _ods_assistant_first_prepare_shared_directory \
            "$data_root/$child_name" \
            "Assistant First shared $child_name directory" \
            "$expected_uid" || return 1
    done
}
