#!/bin/bash
# ============================================================================
# ODS Installer — Logging
# ============================================================================
# Part of: installers/lib/
# Purpose: Log, success, warn, error helpers and elapsed time
#
# Expects: GRN, BGRN, AMB, RED, NC, LOG_FILE, INSTALL_START_EPOCH
# Provides: install_elapsed(), log(), success(), warn(), error()
#
# Modder notes:
#   Change log format or add log levels here.
# ============================================================================

install_elapsed() {
  local now_epoch="${INSTALL_NOW_EPOCH:-$(date +%s)}"
  local start_epoch="${INSTALL_START_EPOCH:-$now_epoch}"
  local secs=$(( now_epoch - start_epoch ))
  local m=$(( secs / 60 ))
  local s=$(( secs % 60 ))
  printf '%dm %02ds' "$m" "$s"
}

_log_out() {
  local msg="$1"
  if [[ -n "${LOG_FILE:-}" ]]; then
    echo -e "$msg" | tee -a "$LOG_FILE"
  else
    echo -e "$msg"
  fi
}

log() { _log_out "${GRN:-}[INFO]${NC:-} ${1:-}"; }
success() { _log_out "${BGRN:-}[OK]${NC:-} ${1:-}"; }
warn() { _log_out "${AMB:-}[WARN]${NC:-} ${1:-}"; }
error() { _log_out "${RED:-}[ERROR]${NC:-} ${1:-}"; exit 1; }
