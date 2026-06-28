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

log() { echo -e "${GRN}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${BGRN}[OK]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${AMB}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }
