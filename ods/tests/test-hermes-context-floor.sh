#!/usr/bin/env bash
# Regression guard for constrained hardware: enabling Hermes must preserve a
# usable runtime profile. Hermes needs a 64K floor, but it must not inflate
# 8GB-class installs to 128K and starve llama-server VRAM.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1" >&2; exit 1; }

run_linux_phase_with_context() {
    local input_context="$1"
    (
        set -euo pipefail
        INTERACTIVE=false
        DRY_RUN=true
        INSTALL_CHOICE=1
        TIER=1
        ENABLE_HERMES=true
        ENABLE_COMFYUI=false
        ENABLE_OPENCLAW=false
        ENABLE_APE=false
        ENABLE_PERPLEXICA=false
        ENABLE_PRIVACY_SHIELD=false
        ENABLE_LANGFUSE=false
        ODS_MODE=local
        MAX_CONTEXT="$input_context"
        MODEL_RECOMMENDATION_REASON="selector chose ${input_context} context"
        SCRIPT_DIR="/tmp/ods-context-floor-no-compose"
        GPU_COUNT=1
        GPU_BACKEND=cpu
        HOST_ARCH=x86_64

        ods_progress() { :; }
        ai_warn() { :; }
        log() { :; }
        warn() { :; }

        # The phase returns after single-GPU assignment setup when sourced.
        # shellcheck source=/dev/null
        source installers/phases/03-features.sh >/dev/null
        printf '%s\n%s\n' "$MAX_CONTEXT" "$MODEL_RECOMMENDATION_REASON"
    )
}

constrained="$(run_linux_phase_with_context 32768)"
constrained_context="$(printf '%s\n' "$constrained" | sed -n '1p')"
constrained_reason="$(printf '%s\n' "$constrained" | sed -n '2p')"
[[ "$constrained_context" == "65536" ]] \
    || fail "Linux Hermes floor should lift 32K selector context to 64K, got ${constrained_context}"
[[ "$constrained_reason" == *"Hermes requires at least 64K context"* ]] \
    || fail "Linux Hermes floor should annotate recommendation reason"
pass "Linux Hermes floor lifts constrained context to 64K"

large="$(run_linux_phase_with_context 131072)"
large_context="$(printf '%s\n' "$large" | sed -n '1p')"
[[ "$large_context" == "131072" ]] \
    || fail "Linux Hermes floor should not reduce existing 128K context, got ${large_context}"
pass "Linux Hermes floor preserves 128K-capable contexts"

grep -Eq 'hermesContextSize[[:space:]]*=[[:space:]]*65536' installers/windows/phases/03-features.ps1 \
    || fail "Windows Hermes floor must be 64K"
grep -Eq 'HERMES_CONTEXT_SIZE=65536' installers/macos/install-macos.sh \
    || fail "macOS Hermes floor must be 64K"
pass "Windows and macOS Hermes floors are 64K"

grep -Eq 'HERMES_CONTEXT_SIZE=.*131072|hermesContextSize[[:space:]]*=[[:space:]]*131072' \
    installers/phases/03-features.sh installers/windows/phases/03-features.ps1 \
    && fail "Linux/Windows Hermes feature phases must not force 128K"
pass "Linux/Windows Hermes feature phases do not force 128K"
