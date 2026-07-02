#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Deploy Orchestrator
# ============================================================================
# Deploy ODS on peer-to-peer GPU marketplaces (Vast.ai)
#
# Target:  Remote GPU instance (NVIDIA, AMD, or CPU-only)
# OS:      Ubuntu 22.04 / 24.04
# License: Apache-2.0 (same as ODS)
#
# Usage:
#   bash setup.sh              # Full install
#   bash setup.sh --resume     # Quick restart (re-apply fixes + start)
#   bash setup.sh --status     # Health check
#   bash setup.sh --info       # Show connection URLs
#   bash setup.sh --fix        # Apply fixes + restart (no reinstall)
#   bash setup.sh --teardown   # Stop all services
#
# This file sources library modules (pure functions) then runs each install
# phase in order. Modules live under:
#   lib/           — reusable function libraries
#   phases/        — sequential install steps (execute on source)
#   subcommands/   — alternative entry points (--teardown, --status, etc.)
#
# Design: adapted from ODS CLAUDE.md for provider environments
#   Let It Crash > KISS > Pure Functions > SOLID
#   set -euo pipefail everywhere. Non-fatal paths use || warn (per
#   CLAUDE.md §4) because on rented hardware, partial stack > dead stack.
# ============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="$(basename "$0")"
DRY_RUN=false

# ── Source libraries ────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/lib/constants.sh"
source "${SCRIPT_DIR}/lib/logging.sh"
source "${SCRIPT_DIR}/lib/environment.sh"
source "${SCRIPT_DIR}/lib/permissions.sh"
source "${SCRIPT_DIR}/lib/services.sh"
source "${SCRIPT_DIR}/lib/networking.sh"
source "${SCRIPT_DIR}/lib/models.sh"
source "${SCRIPT_DIR}/lib/gpu-topology.sh"
source "${SCRIPT_DIR}/lib/compatibility.sh"

# ── Source subcommands ──────────────────────────────────────────────────────
source "${SCRIPT_DIR}/subcommands/teardown.sh"
source "${SCRIPT_DIR}/subcommands/status.sh"
source "${SCRIPT_DIR}/subcommands/resume.sh"
source "${SCRIPT_DIR}/subcommands/fix.sh"
source "${SCRIPT_DIR}/subcommands/info.sh"

# ── Subcommand routing ─────────────────────────────────────────────────────
_route_subcommand() {
  case "${1:-}" in
    --teardown|teardown)  cmd_teardown; exit 0 ;;
    --status|status)      cmd_status;   exit 0 ;;
    --resume|resume)      cmd_resume;   exit 0 ;;
    --fix|fix)            cmd_fix;      exit 0 ;;
    --info|info)          cmd_info;     exit 0 ;;
    --dry-run)            DRY_RUN=true ;;
    --version)            echo "ods-vastai-setup v${VASTAI_VERSION}"; exit 0 ;;
    --help|-h)            _print_help; exit 0 ;;
    --*)                  err "Unknown option: ${1}"; echo "Run 'bash ${SCRIPT_NAME} --help'"; exit 1 ;;
  esac
}

_print_help() {
  echo ""
  echo -e "${BOLD}ODS — Vast.ai Setup v${VASTAI_VERSION}${NC}"
  echo ""
  echo -e "${BOLD}Usage:${NC} bash ${SCRIPT_NAME} [COMMAND]"
  echo ""
  echo -e "${BOLD}Commands:${NC}"
  echo "  (no args)     Full install (first time) or re-install"
  echo "  --resume      Quick restart — re-apply fixes and start services"
  echo "  --status      Health check — show GPU, containers, ports"
  echo "  --info        Show connection URLs and SSH tunnel commands"
  echo "  --fix         Apply latest fixes without full re-install"
  echo "  --teardown    Stop all services"
  echo "  --dry-run     Preview what would happen without making changes"
  echo "  --help        Show this help"
  echo ""
  echo -e "${BOLD}Common scenarios:${NC}"
  echo "  First time:         bash ${SCRIPT_NAME}"
  echo "  SSH dropped:        bash ${SCRIPT_NAME} --resume"
  echo "  Services broken:    bash ${SCRIPT_NAME} --fix"
  echo "  Check status:       bash ${SCRIPT_NAME} --status"
  echo "  Done for the day:   bash ${SCRIPT_NAME} --teardown"
  echo ""
}

# ── Smart re-run detection ──────────────────────────────────────────────────
_check_existing_install() {
  local existing_dir
  existing_dir=$(find_dream_dir 2>&1 || echo "")
  if [[ -n "$existing_dir" && -f "${existing_dir}/.env" ]]; then
    local running_count
    running_count=$(docker ps --format '{{.Names}}' 2>&1 | grep -c '^ods-' || echo 0)
    if [[ "$running_count" -gt 0 ]]; then
      echo ""
      echo -e "${YELLOW}${BOLD}  ODS is already installed (${running_count} services running).${NC}"
      echo ""
      echo -e "  You probably want:"
      echo -e "    ${BOLD}bash ${SCRIPT_NAME} --resume${NC}   → Quick restart + fixes"
      echo -e "    ${BOLD}bash ${SCRIPT_NAME} --fix${NC}      → Apply fixes only"
      echo -e "    ${BOLD}bash ${SCRIPT_NAME} --status${NC}   → Check health"
      echo ""
      echo -n -e "  Continue with full re-install? [y/N] "
      local answer
      read -r -t 15 answer || answer="n"
      if [[ "${answer,,}" != "y" && "${answer,,}" != "yes" ]]; then
        log "Aborted. Use --resume, --fix, --status, or --info."
        exit 0
      fi
      echo ""
    fi
  fi
}

# ── Main install flow ──────────────────────────────────────────────────────
main() {
  _route_subcommand "${1:-}"

  # ── Full install ──────────────────────────────────────────────────────
  echo ""
  echo -e "${CYAN}${BOLD}  ODS — Vast.ai Setup v${VASTAI_VERSION}${NC}"
  echo -e "${DIM}  https://github.com/Light-Heart-Labs/ODS${NC}"
  echo ""

  setup_cleanup_trap
  acquire_lock
  mkdir -p "$(dirname "$LOGFILE")"
  # [NON-FATAL: logging] Setup can proceed even if the logfile is unwritable.
  echo "=== Setup started at $(_ts) ===" >> "$LOGFILE" || warn "logfile write failed (non-fatal)"

  _check_existing_install

  # ── Dry-run mode: preview without executing ────────────────────────
  if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    echo -e "${BOLD}Dry-run mode — no changes will be made.${NC}"
    echo ""
    echo "This setup would:"
    echo "  1.  Detect GPU and validate system requirements"
    echo "  2.  Install dependencies (sudo, git, curl, jq, aria2, etc.)"
    echo "  3.  Create 'dream' user with Docker access"
    echo "  4.  Clone ODS from ${REPO_URL:-Light-Heart-Labs/ODS}"
    echo "  5.  Run ODS installer (non-interactive, 600s timeout)"
    echo "  6.  Apply post-install fixes (permissions, env defaults)"
    echo "  7.  Download/verify GGUF model for llama-server"
    echo "  8.  Apply Vast.ai-specific quirks (/dev/shm, no-systemd)"
    echo "  9.  Start Docker Compose services + health check"
    echo "  10. Bootstrap voice stack (Whisper + Kokoro TTS)"
    echo "  11. Set up reverse proxy (Caddy) + access tunnels"
    echo "  12. Print connection info and SSH tunnel commands"
    echo ""
    echo -e "${BOLD}System:${NC}"
    detect_gpu
    echo "  GPU:    ${GPU_NAME} (${GPU_BACKEND}, ${GPU_VRAM} MB VRAM)"
    echo "  CPU:    $(nproc) cores"
    echo "  Disk:   $(df -BG --output=avail . 2>>"$LOGFILE" | tail -1 | tr -dc '0-9')GB available"
    echo "  Docker: $(docker --version 2>>"$LOGFILE" || echo 'not installed')"
    echo ""
    echo "Run without --dry-run to proceed."
    exit 0
  fi

  # Shared state variables (set by phases, used across phases)
  GPU_BACKEND="" GPU_NAME="" GPU_VRAM="" GPU_COUNT=0
  CPU_COUNT=0 DISK_AVAIL_GB=0 COMPOSE_CMD=""
  REPO_DIR="" ODS_DIR=""

  # ── Execute phases in order ───────────────────────────────────────────
  source "${SCRIPT_DIR}/phases/00-preflight.sh"
  source "${SCRIPT_DIR}/phases/01-dependencies.sh"
  source "${SCRIPT_DIR}/phases/02-user-setup.sh"
  source "${SCRIPT_DIR}/phases/03-repository.sh"
  source "${SCRIPT_DIR}/phases/04-installer.sh"
  source "${SCRIPT_DIR}/phases/05-post-install.sh"
  source "${SCRIPT_DIR}/phases/06-bootstrap-model.sh"
  source "${SCRIPT_DIR}/phases/07-model-optimize.sh"
  source "${SCRIPT_DIR}/phases/08-vastai-quirks.sh"
  source "${SCRIPT_DIR}/phases/09-services.sh"
  source "${SCRIPT_DIR}/phases/10-voice-stack.sh"
  source "${SCRIPT_DIR}/phases/11-access-layer.sh"
  source "${SCRIPT_DIR}/phases/12-summary.sh"
}

main "$@"
