#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Subcommand: status
# ============================================================================
# Part of: p2p-gpu/subcommands/
# Purpose: Display GPU info, container status, download progress
#
# Expects: log(), warn(), err(), find_dream_dir()
# Provides: Health status overview
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

cmd_status() {
  local ds_dir
  ds_dir=$(find_dream_dir) || { err "ODS directory not found"; exit 1; }

  echo -e "\n${BOLD}ODS Status${NC}\n"

  # GPU info
  local gpu_backend
  gpu_backend=$(detect_gpu_backend)
  case "$gpu_backend" in
    nvidia)
      if nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu \
        --format=csv,noheader 2>>"$LOGFILE" | while IFS=',' read -r name mem_total mem_used util; do
        echo -e "  GPU: ${CYAN}${name}${NC} | VRAM: ${mem_used} /${mem_total} | Util: ${util}"
      done; then
        :
      else
        warn "NVIDIA backend detected but nvidia-smi query failed"
      fi
      ;;
    amd)
      if command -v rocm-smi >/dev/null 2>&1; then
        local amd_name amd_vram
        amd_name=$(rocm-smi --showproductname 2>>"$LOGFILE" | grep -oP 'Card series:\s*\K.*' | head -1 || echo "AMD GPU")
        amd_vram=$(rocm-smi --showmeminfo vram 2>>"$LOGFILE" | grep -oP 'Total Memory \(B\):\s*\K[0-9]+' | head -1 || echo "0")
        if [[ "${amd_vram:-0}" -gt 1000000 ]]; then
          amd_vram=$(( amd_vram / 1048576 ))
        fi
        echo -e "  GPU: ${CYAN}${amd_name}${NC} | VRAM: ${amd_vram} MiB"
      else
        warn "AMD backend detected but rocm-smi is not available"
      fi
      ;;
    *)
      echo "  GPU: CPU-only mode (no accelerator detected)"
      ;;
  esac

  echo ""
  docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>&1 | head -20

  echo ""
  local healthy running total
  healthy=$(docker ps --filter "health=healthy" --format '{{.Names}}' | wc -l)
  running=$(docker ps --format '{{.Names}}' | wc -l)
  total=$(docker ps -a --format '{{.Names}}' | grep -c '^ods-' || echo 0)
  echo -e "  Containers: ${GREEN}${healthy}${NC} healthy / ${running} running / ${total} total"

  if pgrep -f "aria2c.*gguf" > /dev/null 2>&1; then
    echo -e "  Model download: ${YELLOW}in progress${NC}"
    local dl_log="${ds_dir}/logs/aria2c-download.log"
    [[ -f "$dl_log" ]] && tail -1 "$dl_log" 2>&1 | sed 's/^/    /'
  fi
  echo ""
}
