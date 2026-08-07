#!/usr/bin/env bash
# ============================================================================
# ODS Mode Switch
# ============================================================================
# Usage: ./mode-switch.sh <local|cloud|hybrid> [--status]
#
# Switches ODS between local/cloud/hybrid modes by updating .env.
# This is the backend for `ods mode <mode>`.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[ods-mode]${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1" >&2; exit 1; }

# Update or add a key=value in .env
# Uses awk index() instead of sed to avoid delimiter collisions
env_set() {
    local key="$1" val="$2"
    local dir
    dir="$(dirname "$ENV_FILE")"
    mkdir -p "$dir"
    local tmp_file
    tmp_file="$(mktemp "$dir/.env.XXXXXX.tmp")"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        awk -v k="$key" -v v="$val" '{
            if (index($0, k "=") == 1) print k "=" v; else print
        }' "$ENV_FILE" > "$tmp_file" && mv -f "$tmp_file" "$ENV_FILE"
    else
        [[ -f "$ENV_FILE" ]] && cp "$ENV_FILE" "$tmp_file"
        echo "${key}=${val}" >> "$tmp_file"
        mv -f "$tmp_file" "$ENV_FILE"
    fi
}

show_status() {
    local current=""
    if [[ -f "$ENV_FILE" ]]; then
        # `grep` exits 1 when the key is absent and the script runs under
        # `set -euo pipefail`, so a bare pipeline here aborted the whole script
        # before the default below could apply — status printed nothing at all.
        current=$(grep -m1 "^ODS_MODE=" "$ENV_FILE" | cut -d= -f2- | tr -d '"\047\r' || true)
    else
        warn ".env not found at $ENV_FILE — showing defaults"
    fi
    echo "Current mode: ${current:-local}"
    echo ""
    echo "Available modes:"
    echo "  local   — Local inference via llama-server (requires GPU/CPU)"
    echo "  cloud   — Cloud APIs via LiteLLM (requires API keys)"
    echo "  hybrid  — Local primary, cloud fallback"
}

switch_mode() {
    local mode="$1"

    # Validate
    case "$mode" in
        local|cloud|hybrid) ;;
        *) error "Unknown mode: $mode. Use: local, cloud, hybrid" ;;
    esac

    [[ -f "$ENV_FILE" ]] || error ".env not found at $ENV_FILE"

    local configured_backend
    configured_backend=$(grep -m1 "^LLM_BACKEND=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"\047\r' || true)
    if [[ "${configured_backend,,}" == "external" ]]; then
        error "External LLM routing is installer-managed. Run './install.sh --no-external-llm' first, then retry the mode switch."
    fi

    # Update .env
    env_set "ODS_MODE" "$mode"

    if [[ "$mode" == "local" ]]; then
        env_set "LLM_API_URL" "http://llama-server:8080"
    else
        env_set "LLM_API_URL" "http://litellm:4000"
        # Auto-enable litellm extension
        local litellm_cf="$SCRIPT_DIR/extensions/services/litellm/compose.yaml"
        local litellm_disabled="${litellm_cf}.disabled"
        if [[ -f "$litellm_disabled" && ! -f "$litellm_cf" ]]; then
            mv "$litellm_disabled" "$litellm_cf"
            success "Auto-enabled litellm for $mode mode"
        fi
    fi

    success "Switched to $mode mode."
    log "Run 'ods restart' to apply."
}

# Called directly or sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:---status}" in
        --status|-s|status) show_status ;;
        --help|-h|help)
            echo "Usage: mode-switch.sh <local|cloud|hybrid|--status>"
            ;;
        *) switch_mode "${1:-}" ;;
    esac
fi
