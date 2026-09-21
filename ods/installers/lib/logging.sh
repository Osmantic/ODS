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
  local secs=$(( now_epoch - INSTALL_START_EPOCH ))
  local m=$(( secs / 60 ))
  local s=$(( secs % 60 ))
  printf '%dm %02ds' "$m" "$s"
}

log() {
  # The cinematic UI narrates the useful state transitions; keep implementation
  # chatter in the log unless verbose output was requested. Plain/CI output
  # retains the traditional foreground log stream for diagnostics.
  if declare -F ods_ui_cinematic >/dev/null 2>&1 \
      && ods_ui_cinematic \
      && [[ "${ODS_UI_VERBOSE:-0}" != "1" ]]; then
    echo -e "${GRN}[INFO]${NC} $1" >> "$LOG_FILE"
  elif declare -F ods_ui_cinematic >/dev/null 2>&1 && ! ods_ui_cinematic; then
    printf '[INFO] %s\n' "$1" | tee -a "$LOG_FILE"
  else
    echo -e "${GRN}[INFO]${NC} $1" | tee -a "$LOG_FILE"
  fi
}
success() {
  if declare -F ods_ui_cinematic >/dev/null 2>&1 && ! ods_ui_cinematic; then
    printf '[OK] %s\n' "$1" | tee -a "$LOG_FILE"
  else
    echo -e "${BGRN}[OK]${NC} $1" | tee -a "$LOG_FILE"
  fi
}
warn() {
  if declare -F ods_ui_cinematic >/dev/null 2>&1 && ! ods_ui_cinematic; then
    printf '[WARN] %s\n' "$1" | tee -a "$LOG_FILE"
  else
    echo -e "${AMB}[WARN]${NC} $1" | tee -a "$LOG_FILE"
  fi
}
error() {
  if declare -F ods_ui_cinematic >/dev/null 2>&1 && ! ods_ui_cinematic; then
    printf '[ERROR] %s\n' "$1" | tee -a "$LOG_FILE"
  else
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"
  fi
  exit 1
}
