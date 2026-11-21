#!/usr/bin/env bash
# Model lifecycle lock helpers (Linux only).
# Children borrow the same open-file description. Release closes only this copy.
ODS_MODEL_LIFECYCLE_LOCK_FD="${ODS_MODEL_LIFECYCLE_LOCK_FD:-}"
ODS_MODEL_LIFECYCLE_LOCK_FILE="${ODS_MODEL_LIFECYCLE_LOCK_FILE:-}"

_ods_model_lifecycle_log() {
  if declare -F log >/dev/null 2>&1; then
    log "$*"
  else
    printf '%s\n' "$*" >&2
  fi
}

_ods_model_lifecycle_helper() {
  local lib_dir helper
  lib_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || return 1
  helper="$lib_dir/../../bin/model_lifecycle_lock.py"
  [ -f "$helper" ] || { _ods_model_lifecycle_log "model lifecycle lock helper missing: $helper"; return 1; }
  command -v python3 >/dev/null 2>&1 || { _ods_model_lifecycle_log "python3 not found"; return 1; }
  printf '%s\n' "$helper"
}

ods_model_lifecycle_lock_file() {
  local install_dir=$1 helper
  helper=$(_ods_model_lifecycle_helper) || return 1
  python3 "$helper" name --install-dir "$install_dir"
}

_ods_model_lifecycle_validate_fd() {
  local install_dir=$1 fd=$2 helper
  helper=$(_ods_model_lifecycle_helper) || return 1
  python3 "$helper" validate --install-dir "$install_dir" --fd "$fd" </dev/null
}

ods_model_lifecycle_lock_acquire() {
  local install_dir=$1 actor=${2:-unknown}
  local fd=${ODS_MODEL_LIFECYCLE_LOCK_FD:-}
  local helper new_fd resolved

  if [ "$(uname -s 2>/dev/null)" != "Linux" ]; then
    return 0
  fi
  command -v flock >/dev/null 2>&1 || { _ods_model_lifecycle_log "flock not found"; return 1; }

  if [ -n "$fd" ]; then
    case "$fd" in
      ''|*[!0-9]*) _ods_model_lifecycle_log "invalid inherited lock fd: $fd"; return 1 ;;
    esac
    if [ "$fd" -lt 3 ]; then
      _ods_model_lifecycle_log "inherited lock fd must be >= 3: $fd"
      return 1
    fi
    _ods_model_lifecycle_validate_fd "$install_dir" "$fd" || {
      _ods_model_lifecycle_log "inherited lock fd $fd failed validation"
      return 1
    }
    flock -xn "$fd" || {
      _ods_model_lifecycle_log "inherited lifecycle descriptor is not available (actor=$actor)"
      return 1
    }
    _ods_model_lifecycle_validate_fd "$install_dir" "$fd" || {
      _ods_model_lifecycle_log "inherited lock fd $fd invalid after lock"
      return 1
    }
    ODS_MODEL_LIFECYCLE_LOCK_FD=$fd
    ODS_MODEL_LIFECYCLE_LOCK_FILE=$(ods_model_lifecycle_lock_file "$install_dir") || return 1
    export ODS_MODEL_LIFECYCLE_LOCK_FD ODS_MODEL_LIFECYCLE_LOCK_FILE
    return 0
  fi

  helper=$(_ods_model_lifecycle_helper) || return 1
  resolved=$(python3 "$helper" path --install-dir "$install_dir") || return 1
  [ -n "$resolved" ] || { _ods_model_lifecycle_log "empty lock path"; return 1; }

  exec {new_fd}<"$resolved" || { _ods_model_lifecycle_log "cannot open lock file: $resolved"; return 1; }

  if ! _ods_model_lifecycle_validate_fd "$install_dir" "$new_fd"; then
    _ods_model_lifecycle_log "lock fd $new_fd failed validation"
    exec {new_fd}<&-
    return 1
  fi

  if ! flock -xn "$new_fd"; then
    _ods_model_lifecycle_log "waiting for model lifecycle lock (actor=$actor)"
    if ! flock -x "$new_fd"; then
      _ods_model_lifecycle_log "failed to acquire model lifecycle lock"
      exec {new_fd}<&-
      return 1
    fi
  fi

  if ! _ods_model_lifecycle_validate_fd "$install_dir" "$new_fd"; then
    _ods_model_lifecycle_log "lock fd $new_fd invalid after acquisition (path replaced?)"
    exec {new_fd}<&-
    return 1
  fi

  ODS_MODEL_LIFECYCLE_LOCK_FD=$new_fd
  ODS_MODEL_LIFECYCLE_LOCK_FILE=$resolved
  export ODS_MODEL_LIFECYCLE_LOCK_FD ODS_MODEL_LIFECYCLE_LOCK_FILE
  return 0
}

ods_model_lifecycle_lock_release() {
  local fd=${ODS_MODEL_LIFECYCLE_LOCK_FD:-}
  if [[ "$fd" =~ ^[0-9]+$ ]] && (( fd >= 3 )); then
    # Closing a borrowed descriptor must not unlock the parent's lease.
    { exec {fd}<&-; } 2>/dev/null || true
  fi
  ODS_MODEL_LIFECYCLE_LOCK_FD=""
  ODS_MODEL_LIFECYCLE_LOCK_FILE=""
}
