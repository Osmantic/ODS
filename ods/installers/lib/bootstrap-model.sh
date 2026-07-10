#!/bin/bash
# ============================================================================
# ODS Installer — Bootstrap Model Library
# ============================================================================
# Part of: installers/lib/
# Purpose: Constants and helpers for the bootstrap model fast-start pattern.
#          Downloads a tiny model first so the user can chat immediately,
#          while the full tier-appropriate model downloads in the background.
#
# Expects: TIER, GGUF_FILE, INSTALL_DIR, NO_BOOTSTRAP, OFFLINE_MODE,
#           ODS_MODE, tier_rank()
# Provides: BOOTSTRAP_* constants, bootstrap_needed()
# ============================================================================

# Bootstrap model: Tier 0 (Qwen 3.5 2B, Q4_K_M quantization, ~1.5GB).
# Hermes requires at least a 64K context window, so fast-start installs keep
# the bootstrap server at that floor instead of the older 8K default.
BOOTSTRAP_GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
BOOTSTRAP_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
BOOTSTRAP_LLM_MODEL="qwen3.5-2b"
BOOTSTRAP_MAX_CONTEXT=65536

# bootstrap_needed — Should we use the fast-start bootstrap pattern?
#
# Returns 0 (true) when ALL of these hold:
#   1. Tier is above 0 (full model is larger than the bootstrap model)
#   2. Full model GGUF file does NOT already exist on disk (including synced)
#   3. --no-bootstrap flag was NOT set
#   4. Not in offline mode (can't download anything)
#   5. Not in cloud mode (no local model needed)
#
bootstrap_needed() {
    local tier_rank
    tier_rank="$(tier_rank "$TIER")"

    # Tier 0: the full model IS the bootstrap model — no point
    [[ "$tier_rank" -le 0 ]] && return 1

    # Full model already on disk (downloaded or synced from external) — skip bootstrap
    [[ -f "${INSTALL_DIR}/data/models/${GGUF_FILE}" ]] && return 1

    # User opted out
    [[ "${NO_BOOTSTRAP:-false}" == "true" ]] && return 1

    # Offline mode — can't download anything
    [[ "${OFFLINE_MODE:-false}" == "true" ]] && return 1

    # Cloud mode — no local model needed
    [[ "${ODS_MODE:-local}" == "cloud" ]] && return 1
    [[ "${LEMONADE_EXTERNAL:-false}" == "true" ]] && return 1

    return 0
}

# sync_bootstrap_if_available — Try to sync the bootstrap model from external
# providers (LM Studio / Ollama) so the fast-start download can be skipped.
# Sets BOOTSTRAP_SYNCED=true when a sync succeeds.
# Should be called after BOOTSTRAP_GGUF_FILE is set and before bootstrap_needed().
sync_bootstrap_if_available() {
    BOOTSTRAP_SYNCED=false
    [[ -f "${INSTALL_DIR}/data/models/${BOOTSTRAP_GGUF_FILE}" ]] && return 0

    local sync_script="${SCRIPT_DIR}/scripts/sync-external-models.sh"
    [[ -f "$sync_script" ]] || return 0

    local result
    result="$(INSTALL_DIR="$INSTALL_DIR" SYNC_EXACT_ONLY=true bash "$sync_script" "$BOOTSTRAP_GGUF_FILE" 2>/dev/null || true)"
    case "$result" in
        synced:*)        BOOTSTRAP_SYNCED=true ;;
        already_present) BOOTSTRAP_SYNCED=true ;;
    esac
}
