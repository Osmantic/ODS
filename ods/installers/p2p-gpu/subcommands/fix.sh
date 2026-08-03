#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Subcommand: fix
# ============================================================================
# Part of: p2p-gpu/subcommands/
# Purpose: Apply fixes without full reinstall (port rebind, network fix,
#          CPU cap, permissions, service restart)
#
# Expects: log(), warn(), err(), find_dream_dir(), detect_gpu_backend(),
#          expose_ports_for_vastai(), apply_post_install_fixes(),
#          start_services(), ensure_whisper_asr_model(), ensure_tts_model_ready(),
#          generate_ssh_tunnel_script(),
#          generate_powershell_tunnel_script(), print_access_info(),
#          get_compose_cmd()
# Provides: All runtime fixes applied and services restarted
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

  cmd_fix() {
  step "Applying fixes (no reinstall)"
  local ds_dir
  ds_dir=$(find_dream_dir) || { err "ODS directory not found. Run full install first."; exit 1; }

  cd "$ds_dir" || exit 1
  detect_gpu
  local gpu_backend="$GPU_BACKEND"

  expose_ports_for_vastai "$ds_dir"

  # Fix stale Docker network
  if docker network inspect ods-network >/dev/null 2>&1; then
    local net_label
    net_label=$(docker network inspect ods-network \
      --format '{{index .Labels "com.docker.compose.network"}}' 2>&1 || echo "")
    if [[ -z "$net_label" ]]; then
      log "Fixing stale ods-network..."
      local compose_cmd
      compose_cmd=$(get_compose_cmd)
      if [[ "$compose_cmd" == "docker compose" ]]; then
        # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
        docker compose down 2>&1 || warn "compose down failed (non-fatal)"
      else
        # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
        docker-compose down 2>&1 || warn "compose down failed (non-fatal)"
      fi
      for cid in $(docker network inspect ods-network \
        -f '{{range .Containers}}{{.Name}} {{end}}' 2>&1 || echo ""); do
        # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
        docker network disconnect -f ods-network "$cid" || warn "disconnect ${cid} failed (non-fatal)"
      done
      # [NON-FATAL: cleanup] Best-effort teardown — partial cleanup is better than none.
      docker network rm ods-network || warn "network rm failed (non-fatal)"
      log "Stale network removed — compose will recreate on next start"
    fi
  fi

  apply_post_install_fixes "$ds_dir" "$gpu_backend"
  if [[ "${GPU_COUNT:-0}" -ge "${MULTIGPU_MIN_GPUS:-2}" ]]; then
    enumerate_gpus
    run_gpu_assignment "$ds_dir" "${ds_dir}/.env"
  fi

  log "Fixes applied. Restarting services..."
  start_services "$ds_dir"
  ensure_whisper_asr_model "$ds_dir"
  ensure_tts_model_ready "$ds_dir"

  generate_ssh_tunnel_script "$ds_dir"
  generate_powershell_tunnel_script "$ds_dir"

  print_access_info "$ds_dir"
  log "Fix complete!"
}
