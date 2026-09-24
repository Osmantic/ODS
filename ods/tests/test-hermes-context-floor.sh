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
    local memory_type="${2:-none}"
    local vram_mb="${3:-0}"
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
        GPU_MEMORY_TYPE="$memory_type"
        GPU_VRAM="$vram_mb"
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

low_vram="$(run_linux_phase_with_context 16384 discrete 4096)"
low_vram_context="$(printf '%s\n' "$low_vram" | sed -n '1p')"
[[ "$low_vram_context" == "16384" ]] \
    || fail "Linux must preserve selector context on sub-8GB discrete GPUs, got ${low_vram_context}"
pass "Linux Hermes floor preserves the VRAM-fit context on a 4GB discrete GPU"

source installers/lib/bootstrap-model.sh
GPU_MEMORY_TYPE=discrete GPU_VRAM=4096 HERMES_CONTEXT_SIZE_EXPLICIT=false
[[ "$(bootstrap_runtime_context 16384)" == "16384" ]] \
    || fail "bootstrap must preserve the selector context on a 4GB discrete GPU"
GPU_MEMORY_TYPE=unified GPU_VRAM=4096
[[ "$(bootstrap_runtime_context 16384)" == "65536" ]] \
    || fail "bootstrap must retain 64K on unified-memory hardware"
GPU_MEMORY_TYPE=discrete GPU_VRAM=4096 HERMES_CONTEXT_SIZE_EXPLICIT=true
[[ "$(bootstrap_runtime_context 16384)" == "65536" ]] \
    || fail "an explicit Hermes context override must retain 64K"
pass "Bootstrap context distinguishes low discrete VRAM from unified and explicit profiles"

grep -Eq 'hermesContextSize[[:space:]]*=[[:space:]]*65536' installers/windows/phases/03-features.ps1 \
    || fail "Windows Hermes floor must be 64K"
grep -Eq 'HERMES_CONTEXT_SIZE=65536' installers/macos/install-macos.sh \
    || fail "macOS Hermes floor must be 64K"
pass "Windows and macOS Hermes floors are 64K"

grep -Eq 'HERMES_CONTEXT_SIZE=.*131072|hermesContextSize[[:space:]]*=[[:space:]]*131072' \
    installers/phases/03-features.sh installers/windows/phases/03-features.ps1 \
    && fail "Linux/Windows Hermes feature phases must not force 128K"
pass "Linux/Windows Hermes feature phases do not force 128K"
