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
    local install_dir="$1" resolved parent base lock_root lock_key

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
    lock_root="${ODS_MODEL_LIFECYCLE_LOCK_ROOT:-/tmp/ods-model-lifecycle-${UID:-$(id -u)}}"
    lock_key="$(printf '%s\0' "$resolved" | cksum | awk '{print $1}')"
    printf '%s/ods-model-lifecycle-%s.lock\n' "${lock_root%/}" "$lock_key"
}

ods_model_lifecycle_lock_acquire() {
    local install_dir="$1" actor="${2:-model lifecycle operation}" lock_file

    [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]] || return 0
    if [[ -n "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" ]]; then
        return 0
    fi
    if ! command -v flock >/dev/null 2>&1; then
        _ods_model_lifecycle_log "Cannot safely run $actor: flock is unavailable."
        return 1
    fi

    lock_file="$(ods_model_lifecycle_lock_file "$install_dir")" || return 1
    if ! (umask 077 && mkdir -p "$(dirname "$lock_file")"); then
        return 1
    fi
    if [[ ! -O "$(dirname "$lock_file")" ]]; then
        _ods_model_lifecycle_log "Refusing model lifecycle lock directory not owned by this user: $(dirname "$lock_file")"
        return 1
    fi
    if ! exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>"$lock_file"; then
        ODS_MODEL_LIFECYCLE_LOCK_FD=""
        _ods_model_lifecycle_log "Cannot open model lifecycle lock for $actor: $lock_file"
        return 1
    fi

    if ! flock -xn "$ODS_MODEL_LIFECYCLE_LOCK_FD"; then
        _ods_model_lifecycle_log "Waiting for another model lifecycle operation before $actor..."
        if ! flock -x "$ODS_MODEL_LIFECYCLE_LOCK_FD"; then
            exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&- 2>/dev/null || true
            ODS_MODEL_LIFECYCLE_LOCK_FD=""
            return 1
        fi
    fi

    ODS_MODEL_LIFECYCLE_LOCK_FILE="$lock_file"
    _ods_model_lifecycle_log "Acquired model lifecycle lock for $actor."
}

ods_model_lifecycle_lock_release() {
    [[ -n "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" ]] || return 0

    flock -u "$ODS_MODEL_LIFECYCLE_LOCK_FD" 2>/dev/null || true
    exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&- 2>/dev/null || true
    ODS_MODEL_LIFECYCLE_LOCK_FD=""
    ODS_MODEL_LIFECYCLE_LOCK_FILE=""
}
