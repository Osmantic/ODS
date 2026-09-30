#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Logging & Lifecycle
# ============================================================================
# Part of: p2p-gpu/lib/
# Purpose: Log/warn/err/step functions, timestamp helper, cleanup trap,
#          flock-based lock acquisition
#
# Expects: LOGFILE, LOCKFILE, RED, GREEN, YELLOW, CYAN, BOLD, NC
# Provides: _ts(), log(), warn(), err(), step(), setup_cleanup_trap(),
#           acquire_lock()
#
# Modder notes:
#   Log writes use append-or-silent ( || : ) to avoid infinite recursion
#
#   if the logfile itself is unwritable. This is the ONE intentional
#
#   deviation from CLAUDE.md §4's "never || true" rule: the logging
#
#   functions ARE the warn() path, so they cannot call warn() on their
#
#   own failure without recursing. The 4 uses below are the only || :
#
#   in the entire toolkit.
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

log() {
  echo -e "${GREEN}[✓]${NC} $*"
  echo "$(_ts) [INFO]  $*" >> "$LOGFILE" || :
}

warn() {
  echo -e "${YELLOW}[!]${NC} $*"
  echo "$(_ts) [WARN]  $*" >> "$LOGFILE" || :
}

err() {
  echo -e "${RED}[✗]${NC} $*" >&2
  echo "$(_ts) [ERROR] $*" >> "$LOGFILE" || :
}

step() {
  echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}\n"
  echo "$(_ts) [STEP]  $*" >> "$LOGFILE" || :
}

# ── Cleanup trap ────────────────────────────────────────────────────────────
setup_cleanup_trap() {
  _vastai_cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
      err "Script failed at line ${BASH_LINENO[0]:-unknown} (exit code: ${exit_code})"
      err "Full log: ${LOGFILE}"
      err "Last 10 lines:"
      tail -10 "$LOGFILE" 2>&1 | sed 's/^/  /' || warn "could not read log tail"
      echo ""
      echo -e "${YELLOW}${BOLD}  What to try next:${NC}"
      echo -e "    ${BOLD}bash $0 --fix${NC}      Apply fixes and restart services"
      echo -e "    ${BOLD}bash $0 --resume${NC}   Quick restart (skip install phases)"
      echo -e "    ${BOLD}bash $0 --status${NC}   Check what's actually running"
      echo ""
    fi
    # Release flock (fd 9 auto-closes on exit)
    exit "$exit_code"
  }
  trap _vastai_cleanup EXIT
  trap 'err "Interrupted by signal"; exit 130' INT TERM HUP
}

# ── Flock-based lock ────────────────────────────────────────────────────────
acquire_lock() {
  exec 9>"$LOCKFILE"
  if ! flock -n 9; then
    err "Another instance is already running."
    echo -e "  ${YELLOW}Wait for it to finish, or force remove:${NC} rm ${LOCKFILE}"
    exit 1
  fi
}

# ── dpkg lock helper (used by phases 00 and 01) ─────────────────────────────
# Waits for the dpkg frontend lock to be released, killing unattended-upgrades
# if it's the holder. Returns 0 when lock is free, 1 on timeout.
_wait_for_dpkg_lock() {
  local max_wait="${1:-90}"

  if ! fuser /var/lib/dpkg/lock-frontend &>/dev/null; then  # stderr expected: fuser probe
    return 0  # Lock is free
  fi

  log "dpkg lock held — attempting to release (timeout ${max_wait}s)"

  # Stop unattended-upgrades if it's the culprit
  if ps aux | grep -q "[u]nattended-upgrade"; then
    log "Stopping unattended-upgrades service..."
    systemctl stop unattended-upgrades 2>>"$LOGFILE" || warn "systemctl stop failed (non-fatal)"
    # Also kill any lingering child processes
    pkill -f unattended-upgrade 2>/dev/null || warn "no unattended-upgrade process found (non-fatal)"  # stderr expected: no matching process
  fi

  # Poll until lock is released
  local elapsed=0
  while fuser /var/lib/dpkg/lock-frontend &>/dev/null; do  # stderr expected: fuser probe
    if [[ $elapsed -ge $max_wait ]]; then
      warn "dpkg lock still held after ${max_wait}s — proceeding with DPkg::Lock::Timeout"
      return 1
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    (( elapsed % 15 == 0 )) && log "Still waiting for dpkg lock... (${elapsed}s / ${max_wait}s)"
  done

  log "dpkg lock released after ${elapsed}s"

  # Clean up any interrupted package state
  if ! dpkg --configure -a 2>>"$LOGFILE"; then
    warn "dpkg --configure -a failed (non-fatal) — DPkg::Lock::Timeout will handle"
  fi

  return 0
}
