#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Subcommand: resume
# ============================================================================
# Part of: p2p-gpu/subcommands/
# Purpose: Quick restart — re-apply fixes and start services
#
# Expects: log(), warn(), err(), find_dream_dir(), detect_gpu_backend(),
#          apply_post_install_fixes(), start_services(),
#          ensure_whisper_asr_model(), ensure_tts_model_ready(),
#          generate_ssh_tunnel_script(), generate_powershell_tunnel_script(),
#          print_access_info()
# Provides: Running ODS with latest fixes applied
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

cmd_resume() {
  step "Resuming ODS"
  local ds_dir
  ds_dir=$(find_dream_dir) || { err "ODS directory not found"; exit 1; }

  cd "$ds_dir" || exit 1
  detect_gpu
  local gpu_backend="$GPU_BACKEND"

  apply_post_install_fixes "$ds_dir" "$gpu_backend"
  if [[ "${GPU_COUNT:-0}" -ge "${MULTIGPU_MIN_GPUS:-2}" ]]; then
    enumerate_gpus
    run_gpu_assignment "$ds_dir" "${ds_dir}/.env"
  fi
  start_services "$ds_dir"
  print_access_info "$ds_dir"

  # Keep the remaining resume steps after the access summary so a later
  # optional failure does not hide the URLs and commands from the terminal.
  ensure_whisper_asr_model "$ds_dir"
  ensure_tts_model_ready "$ds_dir"
  generate_ssh_tunnel_script "$ds_dir"
  generate_powershell_tunnel_script "$ds_dir"
}
