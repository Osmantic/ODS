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

# The macOS Hermes re-selection drops the previous pick's chat template too.
grep -B4 'load_model_selector_env_from_output <<< "$_hermes_env"' installers/macos/install-macos.sh \
    | grep -q 'unset LLAMA_ARG_CHAT_TEMPLATE_FILE' \
    || fail "macOS Hermes re-selection must unset LLAMA_ARG_CHAT_TEMPLATE_FILE before loading the new pick"
pass "macOS Hermes re-selection drops the previous pick's chat template"

grep -Eq 'HERMES_CONTEXT_SIZE=.*131072|hermesContextSize[[:space:]]*=[[:space:]]*131072' \
    installers/phases/03-features.sh installers/windows/phases/03-features.ps1 \
    && fail "Linux/Windows Hermes feature phases must not force 128K"
pass "Linux/Windows Hermes feature phases do not force 128K"

# --- Fit re-check at the floor (installers/lib/model-selector.sh) ----------
# The raise grows the KV cache, so it is re-checked with the real selector
# and catalog against the phase-02 hardware envelope.
fixture_dir="$(mktemp -d)"
trap 'rm -rf "$fixture_dir"' EXIT
mkdir -p "$fixture_dir/scripts" "$fixture_dir/config" "$fixture_dir/installers/lib" \
    "$fixture_dir/extensions/services/dashboard-api" "$fixture_dir/lib"
cp scripts/select-model.py "$fixture_dir/scripts/"
cp config/model-library.json "$fixture_dir/config/"
# The selector names an ODS chat template only when the file ships.
mkdir -p "$fixture_dir/config/llama-server"
cp -R config/llama-server/templates "$fixture_dir/config/llama-server/"
cp installers/lib/model-selector.sh "$fixture_dir/installers/lib/"
cp extensions/services/dashboard-api/model_memory.py \
    extensions/services/dashboard-api/model_selection.py \
    "$fixture_dir/extensions/services/dashboard-api/"
cp lib/python-cmd.sh "$fixture_dir/lib/"

# run_fit_case VRAM_MB RAM_GB TIER LLM GGUF CONTEXT SOURCE [REC_LLM REC_GGUF REC_CONTEXT]
# The REC_* arguments are phase 02's fresh recommendation (INSTALLER_RECOMMENDED_*);
# they default to the configured model, i.e. this run's own pick. A rerun that
# preserved an older active model passes the recommendation it did not apply.
run_fit_case() {
    (
        set -euo pipefail
        INTERACTIVE=false
        DRY_RUN=true
        INSTALL_CHOICE=1
        ENABLE_HERMES=true
        ENABLE_COMFYUI=false
        ENABLE_OPENCLAW=false
        ENABLE_APE=false
        ENABLE_PERPLEXICA=false
        ENABLE_PRIVACY_SHIELD=false
        ENABLE_LANGFUSE=false
        ODS_MODE=local
        SCRIPT_DIR="$fixture_dir"
        LOG_FILE=/dev/null
        GPU_COUNT=0
        GPU_BACKEND=nvidia
        GPU_MEMORY_TYPE=discrete
        HOST_ARCH=x86_64
        MODEL_PROFILE_EFFECTIVE=qwen
        GPU_VRAM="$1"
        RAM_GB="$2"
        TIER="$3"
        LLM_MODEL="$4"
        GGUF_FILE="$5"
        MAX_CONTEXT="$6"
        MODEL_SELECTION_SOURCE="$7"
        INSTALLER_RECOMMENDED_MODEL="${8:-$4}"
        INSTALLER_RECOMMENDED_GGUF="${9:-$5}"
        INSTALLER_RECOMMENDED_CONTEXT="${10:-$6}"
        MODEL_RECOMMENDATION_REASON="selector chose $6 context"
        # What phase 02 loaded with the pick (see CASE_CHAT_TEMPLATE below).
        LLAMA_ARG_CHAT_TEMPLATE_FILE="${CASE_CHAT_TEMPLATE:-}"
        WARNINGS=""

        ods_progress() { :; }
        ai_warn() { WARNINGS="${WARNINGS}$*|"; }
        log() { :; }
        warn() { :; }
        # shellcheck source=/dev/null
        source lib/safe-env.sh
        # shellcheck source=/dev/null
        source installers/phases/03-features.sh >/dev/null
        printf 'MAX_CONTEXT=%s\n' "$MAX_CONTEXT"
        printf 'LLM_MODEL=%s\n' "$LLM_MODEL"
        printf 'MODEL_RUNTIME_PROFILE=%s\n' "${MODEL_RUNTIME_PROFILE:-}"
        printf 'LLAMA_ARG_CACHE_TYPE_K=%s\n' "${LLAMA_ARG_CACHE_TYPE_K:-}"
        printf 'LLAMA_ARG_CHAT_TEMPLATE_FILE=%s\n' "${LLAMA_ARG_CHAT_TEMPLATE_FILE:-}"
        printf 'INSTALLER_RECOMMENDED_CONTEXT=%s\n' "$INSTALLER_RECOMMENDED_CONTEXT"
        printf 'INSTALLER_RECOMMENDED_MODEL=%s\n' "$INSTALLER_RECOMMENDED_MODEL"
        printf 'HERMES_CONTEXT_BELOW_FLOOR=%s\n' "$HERMES_CONTEXT_BELOW_FLOOR"
        printf 'WARNINGS=%s\n' "$WARNINGS"
    )
}

field() { printf '%s\n' "$1" | sed -n "s/^$2=//p"; }

if command -v python3 >/dev/null 2>&1; then
    # (c) RTX 5090 (tower1/tower3): a 32K record of the 27B fits at 64K, so it
    # is raised, and the recommendation the host agent replays says 64K too.
    out="$(run_fit_case 32607 61 3 qwen3.5-27b Qwen3.5-27B-Q4_K_M.gguf 32768 installer)"
    [[ "$(field "$out" MAX_CONTEXT)" == "65536" ]] || fail "5090 27B should be raised to 64K: $out"
    [[ "$(field "$out" INSTALLER_RECOMMENDED_CONTEXT)" == "65536" ]] \
        || fail "MODEL_RECOMMENDED_CONTEXT must record the served 64K, not the pre-raise 32K: $out"
    [[ "$(field "$out" HERMES_CONTEXT_BELOW_FLOOR)" == "false" ]] || fail "5090 27B is not below the floor: $out"
    pass "Linux Hermes floor raises a pick that fits at 64K and records it as the recommendation"

    # (a) 20 GB card: the 27B needs ~20.3 GiB at 64K with F16 KV, so the
    # installer's pick is re-selected: the same model with the Q8 KV profile.
    out="$(run_fit_case 20475 64 3 qwen3.5-27b Qwen3.5-27B-Q4_K_M.gguf 32768 installer)"
    [[ "$(field "$out" MAX_CONTEXT)" == "65536" ]] || fail "20 GB re-selection should serve 64K: $out"
    [[ "$(field "$out" MODEL_RUNTIME_PROFILE)" == "nvidia-20gb-64k-q8-kv" ]] \
        || fail "20 GB re-selection should use the Q8 KV profile: $out"
    [[ "$(field "$out" LLAMA_ARG_CACHE_TYPE_K)" == "q8_0" ]] || fail "20 GB re-selection should load Q8 KV: $out"
    [[ "$(field "$out" INSTALLER_RECOMMENDED_CONTEXT)" == "65536" ]] || fail "re-selection must update the recommendation: $out"
    [[ "$(field "$out" WARNINGS)" == *"was selected at 65536"* ]] || fail "re-selection must be announced: $out"
    pass "Linux Hermes floor re-selects the installer's pick when it does not fit at 64K"

    # (b) A model the owner activated in the Dashboard is never replaced: it
    # keeps the context that fits and the installer says Talk is unavailable.
    out="$(run_fit_case 16376 32 2 qwen3.5-27b Qwen3.5-27B-Q4_K_M.gguf 32768 dashboard)"
    [[ "$(field "$out" MAX_CONTEXT)" == "32768" ]] || fail "a Dashboard model must not be raised past its fit: $out"
    [[ "$(field "$out" LLM_MODEL)" == "qwen3.5-27b" ]] || fail "a Dashboard model must not be replaced: $out"
    [[ "$(field "$out" HERMES_CONTEXT_BELOW_FLOOR)" == "true" ]] || fail "below-floor state must be exported: $out"
    [[ "$(field "$out" WARNINGS)" == *"ODS Talk stays unavailable"* ]] || fail "the cap must say Talk is unavailable: $out"
    pass "Linux Hermes floor keeps a Dashboard model at the context that fits and says so"

    # (d) Nothing installable fits at 64K (NVIDIA 2 GB): keep the pick, say so.
    out="$(run_fit_case 2048 16 0 qwen3.5-2b Qwen3.5-2B-Q4_K_M.gguf 32768 installer)"
    [[ "$(field "$out" MAX_CONTEXT)" == "32768" ]] || fail "no 64K fit must keep the fitting context: $out"
    [[ "$(field "$out" HERMES_CONTEXT_BELOW_FLOOR)" == "true" ]] || fail "no 64K fit must be reported: $out"
    pass "Linux Hermes floor caps when no installable model fits at 64K"

    # (e) The raise is never a fit above the model's native context. phi-4
    # needs ~21.4 GiB at 64K, so memory alone says it fits a 24 GB card, but
    # llama.cpp caps its slot at 16,384. As this run's pick it is re-selected.
    out="$(run_fit_case 24564 64 3 phi-4 phi-4-Q4_K_M.gguf 16384 installer)"
    [[ "$(field "$out" LLM_MODEL)" == "qwen3.5-27b" ]] || fail "phi-4 above its native context must be re-selected: $out"
    [[ "$(field "$out" MAX_CONTEXT)" == "65536" ]] || fail "the re-selected model should serve 64K: $out"
    pass "Linux Hermes floor never raises a model past its native context"

    # (f) A rerun that preserved an older installer pick (source=installer
    # carried over, but not this run's recommendation) keeps it: no silent
    # replacement or new download. Talk is reported unavailable, and the
    # recommendation keeps its own context.
    out="$(run_fit_case 12282 32 2 phi-4 phi-4-Q4_K_M.gguf 16384 installer         qwen3.5-9b Qwen3.5-9B-Q4_K_M.gguf 65536)"
    [[ "$(field "$out" LLM_MODEL)" == "phi-4" ]] || fail "a preserved older pick must not be replaced: $out"
    [[ "$(field "$out" MAX_CONTEXT)" == "16384" ]] || fail "a preserved older pick keeps its context: $out"
    [[ "$(field "$out" HERMES_CONTEXT_BELOW_FLOOR)" == "true" ]] || fail "below-floor state must be exported: $out"
    [[ "$(field "$out" WARNINGS)" == *"ODS Talk stays unavailable"* ]] || fail "the cap must say Talk is unavailable: $out"
    [[ "$(field "$out" INSTALLER_RECOMMENDED_MODEL)" == "qwen3.5-9b" ]] || fail "the recommendation must not change: $out"
    [[ "$(field "$out" INSTALLER_RECOMMENDED_CONTEXT)" == "65536" ]]         || fail "a preserved model's context must not be recorded as the recommendation's: $out"
    pass "Linux Hermes floor keeps a preserved older pick and the recommendation's own context"

    # (g) A preserved pick already at 64K skips the raise; its context is
    # still not recorded for the different recommended model.
    out="$(run_fit_case 49140 128 4 deepseek-r1-distill-llama-70b DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf 65536 installer         qwen3.6-35b-a3b Qwen3.6-35B-A3B-UD-Q4_K_M.gguf 131072)"
    [[ "$(field "$out" INSTALLER_RECOMMENDED_CONTEXT)" == "131072" ]]         || fail "the recommended Qwen3.6-35B-A3B must keep its 131072, not the preserved R1-70B 65536: $out"
    pass "Linux phase 03 records the served context only for this run's own pick"

    # (h) A re-selection replaces the pick's ODS chat template: the new model
    # gets its own, or none. 48 GB: the Qwen3.5-122B-A10B pick does not fit at
    # 64K and Qwen3.6-35B-A3B (no ODS template) is re-selected; 16 GB: the
    # 27B gives way to the 9B and its small-model template.
    templates=/config/llama-server/templates
    out="$(CASE_CHAT_TEMPLATE="$templates/qwen3.5-preserve-thinking.jinja" \
        run_fit_case 49140 128 4 qwen3.5-122b-a10b Qwen3.5-122B-A10B-Q4_K_M-00001-of-00003.gguf 32768 installer)"
    [[ "$(field "$out" LLM_MODEL)" == "qwen3.6-35b-a3b" ]] || fail "48 GB 122B-A10B should be re-selected: $out"
    [[ -z "$(field "$out" LLAMA_ARG_CHAT_TEMPLATE_FILE)" ]] \
        || fail "a re-selected model must not keep the previous pick's chat template: $out"
    out="$(CASE_CHAT_TEMPLATE="$templates/qwen3.5-preserve-thinking.jinja" \
        run_fit_case 16376 64 3 qwen3.5-27b Qwen3.5-27B-Q4_K_M.gguf 32768 installer)"
    [[ "$(field "$out" LLM_MODEL)" == "qwen3.5-9b" ]] || fail "16 GB 27B should be re-selected: $out"
    [[ "$(field "$out" LLAMA_ARG_CHAT_TEMPLATE_FILE)" == "$templates/qwen3.5-small-preserve-thinking.jinja" ]] \
        || fail "the re-selected 9B must get its own chat template: $out"
    pass "Linux Hermes floor re-selection replaces the previous pick's chat template"
else
    echo "  SKIP: python3 unavailable; fit re-check cases need the real selector"
fi
