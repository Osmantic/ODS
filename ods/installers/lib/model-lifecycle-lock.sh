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
    # Append mode: opening the lock file must not truncate it, or a waiting
    # process would erase the recorded holder identity before timing out.
    if ! exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>>"$lock_file"; then
        ODS_MODEL_LIFECYCLE_LOCK_FD=""
        _ods_model_lifecycle_log "Cannot open model lifecycle lock for $actor: $lock_file"
        return 1
    fi

    if ! flock -xn "$ODS_MODEL_LIFECYCLE_LOCK_FD"; then
        local wait_seconds="${ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS:-3600}"
        [[ "$wait_seconds" =~ ^[0-9]+$ ]] || wait_seconds=3600
        _ods_model_lifecycle_log "Waiting for another model lifecycle operation before $actor..."
        # Bound the wait: a wedged holder must not block this operation
        # forever. 3600s covers even very slow full-model downloads; operators
        # can tune via ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS.
        if ! flock -x -w "$wait_seconds" "$ODS_MODEL_LIFECYCLE_LOCK_FD"; then
            local holder=""
            holder="$(tail -n 1 "$lock_file" 2>/dev/null || true)"
            # The redirect must be scoped to the group: a bare
            # `exec fd>&- 2>/dev/null` would silence stderr permanently.
            { exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&-; } 2>/dev/null || true
            ODS_MODEL_LIFECYCLE_LOCK_FD=""
            if [[ -n "$holder" ]]; then
                _ods_model_lifecycle_log "Timed out after ${wait_seconds}s waiting for $actor; last lock holder: $holder"
            else
                _ods_model_lifecycle_log "Timed out after ${wait_seconds}s waiting for $actor."
            fi
            return 1
        fi
    fi

    # Record the holder so a later waiter (or post-mortem) can identify who
    # blocked the lifecycle lock. One short line per acquisition.
    printf 'pid=%s actor=%s at=%s\n' "$$" "$actor" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        >>"$lock_file" 2>/dev/null || true

    ODS_MODEL_LIFECYCLE_LOCK_FILE="$lock_file"
    _ods_model_lifecycle_log "Acquired model lifecycle lock for $actor."
}

ods_model_lifecycle_lock_release() {
    [[ -n "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" ]] || return 0

    flock -u "$ODS_MODEL_LIFECYCLE_LOCK_FD" 2>/dev/null || true
    { exec {ODS_MODEL_LIFECYCLE_LOCK_FD}>&-; } 2>/dev/null || true
    ODS_MODEL_LIFECYCLE_LOCK_FD=""
    ODS_MODEL_LIFECYCLE_LOCK_FILE=""
}
