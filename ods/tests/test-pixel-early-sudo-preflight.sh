#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/03-features.sh"

run_case() (
    set -euo pipefail
    local selected="$1" dry_run="$2"
    INTERACTIVE=false
    DRY_RUN="$dry_run"
    ENABLE_PIXEL="$selected"
    ENABLE_HERMES=false
    ENABLE_OPENCLAW=false
    ENABLE_COMFYUI=false
    ENABLE_LANGFUSE=false
    ENABLE_RECOMMENDED=false
    ODS_MODE=local
    TIER=0
    GPU_COUNT=0
    SCRIPT_DIR="$(mktemp -d)"
    INSTALL_DIR="$SCRIPT_DIR/install"
    trap 'rm -rf -- "$SCRIPT_DIR"' EXIT

    ods_progress() { :; }
    ai() { :; }
    ai_bad() { printf '%s\n' "$*" >&2; }
    ai_warn() { :; }
    log() { :; }
    warn() { :; }
    ods_sudo_available() { return 1; }
    ods_pixel_resolve_enablement() {
        if [[ "$1" == true ]]; then printf 'pixel\n'; else printf 'hermes\n'; fi
    }
    ods_pixel_model_route_class() { printf 'local\n'; }

    source "$PHASE"
)

if output="$(run_case true false 2>&1)"; then
    echo 'FAIL: selected Pixel continued without privileged setup' >&2
    exit 1
fi
[[ "$output" == *'Pixel requires privileged systemd and group setup'* ]] || {
    echo "FAIL: missing actionable Pixel privilege error: $output" >&2
    exit 1
}
echo 'PASS: selected Pixel fails in feature selection when sudo is unavailable'

run_case false false >/dev/null
echo 'PASS: install without Pixel continues without sudo'

run_case true true >/dev/null
echo 'PASS: dry run previews Pixel without requiring sudo'
