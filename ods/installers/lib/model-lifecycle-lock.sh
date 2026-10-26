#!/usr/bin/env bash
# Serialize Linux installer model configuration with background model activation.

ODS_MODEL_LIFECYCLE_LOCK_FD="${ODS_MODEL_LIFECYCLE_LOCK_FD:-}"
ODS_MODEL_LIFECYCLE_LOCK_FILE="${ODS_MODEL_LIFECYCLE_LOCK_FILE:-}"

_ods_model_lifecycle_log() {
    if declare -F log >/dev/null 2>&1; then
        log "$*"
    else
        printf '[MODEL-LIFECYCLE] %s\n' "$*" >&2
    fi
}

ods_model_lifecycle_lock_file() {
    local install_dir="$1" resolved parent base lock_root lock_key lock_uid

    resolved="${install_dir%/}"
    if [[ -d "$install_dir" ]]; then
        resolved="$(cd "$install_dir" && pwd -P)" || return 1
    else
        parent="$(dirname "$install_dir")"
        base="$(basename "$install_dir")"
        if [[ -d "$parent" ]]; then
            resolved="$(cd "$parent" && pwd -P)/$base" || return 1
        fi
    fi

    # Keep the default independent of XDG_RUNTIME_DIR. The background upgrader
    # and a later SSH/systemd installer can have different environment values
    # while still operating on the same installation.
    lock_uid="$(stat -c %u "$resolved" 2>/dev/null || id -u)"
    lock_root="${ODS_MODEL_LIFECYCLE_LOCK_ROOT:-/tmp/ods-model-lifecycle-${lock_uid}}"
    lock_key="$(printf '%s\0' "$resolved" | cksum | awk '{print $1}')"
    printf '%s/ods-model-lifecycle-%s.lock\n' "${lock_root%/}" "$lock_key"
}

ods_model_lifecycle_lock_acquire() {
    local install_dir="$1" actor="${2:-model lifecycle operation}" helper reply read_fd parent_pid wait_seconds
    [[ "$(uname -s 2>/dev/null || true)" == Linux ]] || return 0
    [[ -z "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" ]] || return 0
    helper="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../bin" && pwd)/model_lifecycle_lock.py"
    [[ -f "$helper" && ! -L "$helper" ]] || return 1
    parent_pid="$BASHPID"
    wait_seconds="${ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS:-300}"
    [[ "$wait_seconds" =~ ^[0-9]+$ ]] && (( wait_seconds <= 300 )) || return 1
    # The helper opens with O_NOFOLLOW relative to a validated directory fd.
    # In particular root never follows/chowns a precreated /tmp symlink.
    # macOS Bash 3.2 parses this shared library before the Linux-only guard.
    # Keep the Bash 4 coprocess syntax inside a literal; arguments expand only
    # when that literal runs, with their existing quoting preserved.
    eval 'coproc ODS_MODEL_LOCK { exec python3 "$helper" hold "$install_dir" "$parent_pid" "$wait_seconds"; }'
    ODS_MODEL_LIFECYCLE_LOCK_PID="$ODS_MODEL_LOCK_PID"
    exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&"${ODS_MODEL_LOCK[1]}"
    read_fd="${ODS_MODEL_LOCK[0]}"
    if ! IFS= read -r -t "$((wait_seconds + 10))" reply <&"$read_fd" || [[ "$reply" != locked ]]; then
        ods_model_lifecycle_lock_release
        _ods_model_lifecycle_log "Cannot safely acquire model lifecycle lock for $actor."
        return 1
    fi
    ODS_MODEL_LIFECYCLE_LOCK_FILE="$(ods_model_lifecycle_lock_file "$install_dir")"
    _ods_model_lifecycle_log "Acquired model lifecycle lock for $actor."
}

ods_model_lifecycle_lock_release() {
    [[ -n "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" ]] || return 0
    printf 'release\n' 1>&"$ODS_MODEL_LIFECYCLE_LOCK_FD" 2>/dev/null || true
    { exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&-; } 2>/dev/null || true
    if [[ -n "${ODS_MODEL_LIFECYCLE_LOCK_PID:-}" ]]; then
        wait "$ODS_MODEL_LIFECYCLE_LOCK_PID" 2>/dev/null || true
    fi
    ODS_MODEL_LIFECYCLE_LOCK_FD=""
    ODS_MODEL_LIFECYCLE_LOCK_FILE=""
    ODS_MODEL_LIFECYCLE_LOCK_PID=""
}
