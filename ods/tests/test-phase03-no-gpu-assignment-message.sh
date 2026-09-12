#!/usr/bin/env bash
# Phase 03 must not report "Single GPU detected" on a host with no GPU.
#
# The single-GPU assignment branch was guarded with `GPU_COUNT -le 1`, which
# also matches 0, so every CPU-only install logged
# "Single GPU detected — non-NVIDIA backend, skipping GPU assignment." right
# after "No GPU detected". Source the real phase with the same stub set the
# multi-GPU harness uses and check the zero-GPU and single-GPU messages.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$FEATURES_PHASE" ]] || fail "missing phase 03: $FEATURES_PHASE"

# Static guard: the zero-GPU branch must sit before the single-GPU branch.
zero_line="$(grep -n 'GPU_COUNT:-0}" -eq 0' "$FEATURES_PHASE" | head -1 | cut -d: -f1)"
single_line="$(grep -n '"$GPU_COUNT" -le 1' "$FEATURES_PHASE" | head -1 | cut -d: -f1)"
[[ -n "$zero_line" && -n "$single_line" && "$zero_line" -lt "$single_line" ]] \
    || fail "phase 03 must handle GPU_COUNT=0 before the single-GPU (-le 1) branch"
pass "zero-GPU branch precedes the single-GPU branch"

# Phase 03 needs Bash 4+ (associative arrays); the installer guarantees it.
if [[ "${BASH_VERSINFO[0]:-0}" -lt 4 ]]; then
    echo "[SKIP] phase 03 requires Bash 4+; this host only has Bash ${BASH_VERSION} (brew install bash)"
    exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

run_phase() {
    # $1 = GPU_COUNT, $2 = GPU_BACKEND. Prints the phase's log lines.
    local gpu_count="$1" gpu_backend="$2"
    mkdir -p "$tmp_dir/install" "$tmp_dir/scripts"
    HARNESS_TMP="$tmp_dir" HARNESS_GPU_COUNT="$gpu_count" HARNESS_GPU_BACKEND="$gpu_backend" \
        bash -c '
set -euo pipefail
INTERACTIVE=false
DRY_RUN=true
INSTALL_CHOICE=1
TIER=1
ODS_MODE=local
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_RECOMMENDED=false
ENABLE_HERMES=false
ENABLE_OPENCLAW=false
ENABLE_COMFYUI=false
ENABLE_APE=false
ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false
ENABLE_LANGFUSE=false
ENABLE_BRAVE_SEARCH=false
GPU_COUNT="$HARNESS_GPU_COUNT"
GPU_BACKEND="$HARNESS_GPU_BACKEND"
HOST_ARCH=amd64
HOST_PAGE_SIZE=4096
INSTALL_DIR="$HARNESS_TMP/install"
SCRIPT_DIR="$HARNESS_TMP"
LLM_MODEL_SIZE_MB=6000
MAX_CONTEXT=8192
VERBOSE=false
DEBUG=false
AMB=
BGRN=
DIM=
GRN=
NC=
RED=
WHT=
GPU_TOPOLOGY_JSON=""

ods_progress() { :; }
show_phase() { :; }
show_install_menu() { :; }
ai_warn() { :; }
log() { printf "LOG: %s\n" "$*"; }
warn() { printf "WARN: %s\n" "$*"; }
success() { :; }
chapter() { :; }
bootline() { :; }
signal() { :; }
get_rank() { printf "10\n"; }
error() { printf "ERROR: %s\n" "$*" >&2; return 1; }

# shellcheck source=/dev/null
source "$1"
echo PHASE03_COMPLETED
' _ "$FEATURES_PHASE"
}

# Case 1: no GPU (CPU-only install).
out="$(run_phase 0 cpu)" || fail "phase 03 failed with GPU_COUNT=0 (CPU-only)"
grep -q 'PHASE03_COMPLETED' <<<"$out" || fail "phase 03 did not complete with GPU_COUNT=0"
grep -q 'No GPU detected — skipping GPU assignment' <<<"$out" \
    || fail "GPU_COUNT=0 must log the no-GPU skip message; got: $(grep 'GPU' <<<"$out" | tr '\n' '|')"
grep -q 'Single GPU detected' <<<"$out" \
    && fail "GPU_COUNT=0 must not log 'Single GPU detected'"
pass "CPU-only install logs the no-GPU skip, not 'Single GPU detected'"

# Case 2: one non-NVIDIA GPU keeps its existing message.
out="$(run_phase 1 amd)" || fail "phase 03 failed with GPU_COUNT=1 (amd)"
grep -q 'PHASE03_COMPLETED' <<<"$out" || fail "phase 03 did not complete with GPU_COUNT=1"
grep -q 'Single GPU detected — non-NVIDIA backend, skipping GPU assignment' <<<"$out" \
    || fail "GPU_COUNT=1 with a non-NVIDIA backend must keep the single-GPU message"
grep -q 'No GPU detected — skipping' <<<"$out" \
    && fail "GPU_COUNT=1 must not log the no-GPU skip message"
pass "single non-NVIDIA GPU keeps the single-GPU skip message"
