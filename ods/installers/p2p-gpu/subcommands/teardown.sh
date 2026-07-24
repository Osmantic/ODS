#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Subcommand: teardown
# ============================================================================
# Part of: ods/installers/p2p-gpu/subcommands/
# Purpose: Stop all containers and background processes to stop all services
#
# Expects: log(), warn(), err(), find_dream_dir(), get_compose_cmd(),
#          _kill_stored_pid(), PIDFILE_DIR, SCRIPT_NAME
# Provides: Clean shutdown of all ODS services
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

cmd_teardown() {
  step "Teardown — stopping all services"
  local ds_dir
  ds_dir=$(find_dream_dir) || { err "ODS directory not found"; exit 1; }

  cd "$ds_dir" || exit 1

  if [[ -f "docker-compose.base.yml" ]]; then
    local compose_cmd
    compose_cmd=$(get_compose_cmd)
    if [[ "$compose_cmd" == "docker compose" ]]; then
      # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
      docker compose down --remove-orphans 2>&1 || warn "Compose down had warnings (non-fatal)"
    else
      # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
      docker-compose down --remove-orphans 2>&1 || warn "Compose down had warnings (non-fatal)"
    fi
  fi

  # [FIX: pkill] Use PID-file based cleanup instead of pkill -f
  _kill_stored_pid "aria2c-model"
  _kill_stored_pid "model-swap-watcher"
  _kill_stored_pid "cloudflared"

  log "All services stopped. Storage billing continues."
  log "To fully stop billing: delete the instance from the provider console."
  echo ""
  echo -e "${BOLD}Data preserved at:${NC} ${ds_dir}/data/"
  echo -e "${BOLD}To resume:${NC} bash ${SCRIPT_NAME} --resume"
}
