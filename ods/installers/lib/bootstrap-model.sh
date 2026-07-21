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
BOOTSTRAP_GGUF_SHA256="aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223"
BOOTSTRAP_LLM_MODEL="qwen3.5-2b"
BOOTSTRAP_MAX_CONTEXT=65536

# bootstrap_needed — Should we use the fast-start bootstrap pattern?
#
# Returns 0 (true) when ALL of these hold:
#   1. Tier is above 0 (full model is larger than the bootstrap model)
#   2. Full model GGUF file does NOT already exist on disk
#   3. --no-bootstrap flag was NOT set
#   4. Not in offline mode (can't download anything)
#   5. Not in cloud mode (no local model needed)
#
bootstrap_needed() {
    local tier_rank
    tier_rank="$(tier_rank "$TIER")"

    # Tier 0: the full model IS the bootstrap model — no point
    [[ "$tier_rank" -le 0 ]] && return 1

    # Full model already on disk — skip bootstrap, use it directly
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
