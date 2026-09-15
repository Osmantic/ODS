#!/bin/bash
# ============================================================================
# ODS Installer — Phase 09: Offline Mode Setup
# ============================================================================
# Part of: installers/phases/
# Purpose: Configure M1 offline/air-gapped operation
#
# Expects: OFFLINE_MODE, DRY_RUN, INSTALL_DIR, ENABLE_OPENCLAW, LOG_FILE,
#           chapter(), ai(), ai_ok(), ai_warn(), log()
# Provides: Offline mode marker, M1 config files, embedded embeddings
#
# Modder notes:
#   Add offline-specific configuration or bundled models here.
# ============================================================================

ods_progress 65 "offline" "Configuring offline mode"
if [[ "$OFFLINE_MODE" == "true" ]] && $DRY_RUN; then
    log "[DRY RUN] Would configure offline/air-gapped mode (M1)"
    log "[DRY RUN] Would create offline mode marker, disable cloud features"
    [[ "$ENABLE_OPENCLAW" == "true" ]] && log "[DRY RUN] Would create OpenClaw M1 config"
    log "[DRY RUN] Would pre-download GGUF embeddings for memory_search"
elif [[ "$OFFLINE_MODE" == "true" ]] && ! $DRY_RUN; then
    chapter "CONFIGURING OFFLINE MODE (M1)"

    # A previous successful install must not make a failed rerun look ready.
    # Recreate the marker only after the required asset is validated below.
    rm -f -- "$INSTALL_DIR/.offline-mode"

    # Disable any cloud-dependent features in .env
    _sed_i 's/^BRAVE_API_KEY=.*/BRAVE_API_KEY=/' "$INSTALL_DIR/.env" 2>/dev/null || true
    _sed_i 's/^ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=/' "$INSTALL_DIR/.env" 2>/dev/null || true
    _sed_i 's/^OPENAI_API_KEY=.*/OPENAI_API_KEY=/' "$INSTALL_DIR/.env" 2>/dev/null || true

    # Add offline mode config
    cat >> "$INSTALL_DIR/.env" << 'OFFLINE_EOF'

#=============================================================================
# M1 Offline Mode Configuration
#=============================================================================
OFFLINE_MODE=true

# Disable telemetry and update checks
DISABLE_TELEMETRY=true
DISABLE_UPDATE_CHECK=true

# Use local RAG instead of web search
WEB_SEARCH_ENABLED=false
LOCAL_RAG_ENABLED=true
OFFLINE_EOF

    # Create OpenClaw M1 config if OpenClaw is enabled
    if [[ "$ENABLE_OPENCLAW" == "true" ]]; then
        mkdir -p "$INSTALL_DIR/config/openclaw"
        cat > "$INSTALL_DIR/config/openclaw/openclaw-m1.yaml" << 'M1_EOF'
# OpenClaw M1 Mode Configuration
# Fully offline operation - no cloud dependencies

memorySearch:
  enabled: true
  # Uses bundled GGUF embeddings (auto-downloaded during install)
  # No external API calls

# Disable web search (not available offline)
# Use local RAG with Qdrant instead
webSearch:
  enabled: false

# Local inference only
inference:
  provider: local
  baseUrl: http://llama-server:8080/v1
M1_EOF
        ai_ok "OpenClaw M1 config created"
    fi

    # Pre-download GGUF embeddings for memory_search.  Offline mode is only
    # valid when this required asset is present and structurally valid: a
    # failed download must not leave an install that cannot work offline.
    ai "Pre-downloading GGUF embeddings for offline memory_search..."
    mkdir -p "$INSTALL_DIR/models/embeddings"

    EMBED_FILE="$INSTALL_DIR/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf"
    EMBED_URL="https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf"
    _embedding_valid() {
        [[ -s "$1" ]] || return 1
        [[ "$(head -c 4 "$1" 2>/dev/null)" == "GGUF" ]]
    }
    if _embedding_valid "$EMBED_FILE"; then
        log "Embeddings already downloaded"
    else
        command -v curl >/dev/null 2>&1 || error "Offline mode requires curl to download the embedding asset."
        _embed_tmp="${EMBED_FILE}.tmp.$$"
        rm -f -- "$_embed_tmp"
        if ! curl --fail --silent --show-error --location --retry 3 --max-time 3600 \
            -o "$_embed_tmp" "$EMBED_URL"; then
            rm -f -- "$_embed_tmp"
            error "Could not download the required offline embedding asset."
        fi
        if ! _embedding_valid "$_embed_tmp"; then
            rm -f -- "$_embed_tmp"
            error "Downloaded offline embedding asset is missing or not a GGUF file."
        fi
        mv -f -- "$_embed_tmp" "$EMBED_FILE" || error "Could not install the offline embedding asset."
    fi
    # Do not advertise air-gapped readiness until all required assets pass.
    touch "$INSTALL_DIR/.offline-mode"

    # Whisper STT model: Phase 12 pre-downloads it by POSTing to the running
    # Speaches API, but offline-mode users often disconnect BEFORE Phase 12
    # completes, or they run 'ods stop' before network becomes unavailable.
    # We can't pre-download from HuggingFace directly in Phase 9 without a
    # huggingface_hub Python dep, so surface the requirement loudly here and
    # point users at the 'ods stt download' CLI (added in the same PR).
    if [[ "$ENABLE_VOICE" == "true" ]]; then
        ai_warn "Offline mode + voice enabled: Whisper STT model is NOT pre-downloaded by Phase 9"
        log "  The installer's Phase 12 will still attempt the download while online,"
        log "  but if you go offline before it completes, STT will 404 on first use."
        log "  To ensure the model is cached before disconnecting, run after install:"
        log "    ods stt download"
        log "  Or use 'scripts/pre-download.sh --with-voice' to pre-cache before install."
    fi

    # Offline docs already copied by rsync/cp block above
    ai_ok "Offline mode configured"
    log "After installation, disconnect from internet for fully air-gapped operation"
    log "See docs/M1-OFFLINE-MODE.md for offline operation guide"
fi
