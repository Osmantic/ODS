#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 08: Vast.ai Quirks
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: No-systemd workaround, /dev/shm remount, OpenCode crash-loop fix
#
# Expects: ODS_DIR, ODS_USER, log(), warn()
# Provides: Vast.ai-specific environment fixes applied
#
# Fixes covered: #18 (/dev/shm), #21 (no systemd), #22 (OpenCode crash-loop),
#                #24 (/dev/shm too small)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 8/12: Applying Vast.ai-specific fixes"

# ── No systemd ─────────────────────────────────────────────────────────────
if ! command -v systemctl &>/dev/null && ! pidof systemd &>/dev/null; then
  log "No systemd detected — Vast.ai environment confirmed"
  ods_cli="${ODS_DIR}/ods-cli"
  if [[ -x "$ods_cli" ]]; then
    # Start host agent early on no-systemd hosts so model downloads and dashboard
    # operations are available before the compose stack fully settles.
    # [NON-FATAL: host-agent] Agent start can be retried in later phases.
    su - "$ODS_USER" -c "cd ${ODS_DIR} && ODS_HOME=${ODS_DIR} ./ods-cli agent start" 2>&1 || \
      warn "Host agent start failed (non-fatal — will retry in phase 09)"
  fi
fi

# ── OpenCode crash-loop disable ────────────────────────────────────────────
if docker ps -a --format '{{.Names}} {{.Status}}' 2>&1 | grep -q 'ods-opencode.*Restarting'; then
  warn "OpenCode is crash-looping — disabling to unblock other services"
  ods_cli="${ODS_DIR}/ods-cli"
  if [[ -x "$ods_cli" ]]; then
    # [NON-FATAL: opencode] Individual service failure does not block others.
    su - "$ODS_USER" -c "cd ${ODS_DIR} && ./ods-cli disable opencode" 2>&1 \
      || warn "ods-cli disable opencode failed (non-fatal)"
  else
    # [NON-FATAL: opencode] Individual service failure does not block others.
    docker stop ods-opencode || warn "opencode stop failed (non-fatal)"
    # [NON-FATAL: opencode] Individual service failure does not block others.
    docker rm ods-opencode || warn "opencode rm failed (non-fatal)"
  fi
fi

# ── Shared memory fix ─────────────────────────────────────────────────────
shm_size_kb=$(df /dev/shm 2>&1 | awk 'NR==2{print $2}' || echo 0)
if [[ "${shm_size_kb:-0}" -lt 1048576 ]]; then
  shm_mb=$(( shm_size_kb / 1024 ))
  warn "/dev/shm is only ${shm_mb} MB — GPU containers may be memory-starved"
  # [NON-FATAL: perf] Remount is a performance optimization only.
  mount -o remount,size=4G /dev/shm || warn "/dev/shm remount failed (non-fatal)"
fi

# ── Pre-pull Docker images ─────────────────────────────────────────────────
prepull_docker_images "$ODS_DIR"

log "Vast.ai environment fixes applied"
