#!/bin/bash
# ============================================================================
# ODS Installer — Phase 02b: External Service Detection
# ============================================================================
# Part of: installers/phases/
# Purpose: Detect running Ollama or LM Studio services and reuse them.
#
# Expects: SCRIPT_DIR, LOG_FILE, INTERACTIVE, DRY_RUN, ODS_MODE, GGUF_FILE
# Provides: EXTERNAL_LLM_URL, EXTERNAL_LLM_PROVIDER, EXTERNAL_LLM_MODEL,
#           SKIP_MODEL_DOWNLOAD
# ============================================================================

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "${SCRIPT_DIR:-}/lib/safe-env.sh"

ods_progress 15 "detection" "Detecting external LLM services"
chapter "EXTERNAL SERVICE DETECTION"

# Guard: if external LLM is already configured (e.g. from existing .env on upgrade), preserve and set skip model download
if [[ -n "${EXTERNAL_LLM_URL:-}" ]]; then
    # Don't assume — re-probe even on rerun
    _probe_url="${EXTERNAL_LLM_URL/host.docker.internal/127.0.0.1}"
    if ! curl -sf --max-time 2 "${_probe_url}/api/tags" > /dev/null 2>&1 && \
       ! curl -sf --max-time 2 "${_probe_url}/v1/models" > /dev/null 2>&1; then
        warn "EXTERNAL_LLM_URL is set but service is not responding — falling back to local llama-server"
        unset EXTERNAL_LLM_URL
        unset EXTERNAL_LLM_PROVIDER
        unset EXTERNAL_LLM_MODEL
        unset SKIP_MODEL_DOWNLOAD
    else
        log "EXTERNAL_LLM_URL already configured ($EXTERNAL_LLM_URL) and responding — ensuring SKIP_MODEL_DOWNLOAD=true"
        export EXTERNAL_LLM_URL
        export EXTERNAL_LLM_PROVIDER
        export EXTERNAL_LLM_MODEL
        export SKIP_MODEL_DOWNLOAD="true"
        resolve_compose_config
        return 0
    fi
fi

# Ensure library functions are available
if ! declare -f detect_ollama >/dev/null; then
    if [[ -f "${SCRIPT_DIR:-}/installers/lib/external-services.sh" ]]; then
        source "${SCRIPT_DIR:-}/installers/lib/external-services.sh"
    elif [[ -f "${SCRIPT_DIR:-}/lib/external-services.sh" ]]; then
        source "${SCRIPT_DIR:-}/lib/external-services.sh"
    fi
fi

# Skip detection in cloud mode or if interactive is disabled (unless preconfigured above)
if [[ "${ODS_MODE:-local}" == "cloud" ]] || [[ "${LEMONADE_EXTERNAL:-false}" == "true" ]]; then
    return 0
fi

log "Scanning for active local LLM services (Ollama, LM Studio)..."

ollama_models=$(detect_ollama) || ollama_models=""
lmstudio_models=$(detect_lmstudio) || lmstudio_models=""

matched_model=""
provider=""
api_url=""

if [[ -n "$ollama_models" ]]; then
    matched_model=$(find_matching_external_model "${GGUF_FILE:-}" "$ollama_models") || matched_model=""
    if [[ -n "$matched_model" ]]; then
        provider="ollama"
        api_url="http://host.docker.internal:11434"
    fi
fi

if [[ -z "$matched_model" && -n "$lmstudio_models" ]]; then
    matched_model=$(find_matching_external_model "${GGUF_FILE:-}" "$lmstudio_models") || matched_model=""
    if [[ -n "$matched_model" ]]; then
        provider="lmstudio"
        api_url="http://host.docker.internal:1234"
    fi
fi

if [[ -n "$matched_model" ]]; then
    ai_ok "Detected active external LLM service: $provider"
    ai "Found matching model: $matched_model"

    if [[ "${INTERACTIVE:-false}" == "true" ]] && [[ "${DRY_RUN:-false}" != "true" ]]; then
        echo
        ai "ODS can reuse this service instead of running a local llama-server."
        ai "This will skip the 5GB+ model download and save system memory/GPU resources."
        echo
        read -p "  Reuse the running external $provider service? [Y/n] " -r reply < /dev/tty
        echo
        case "$reply" in
            [Nn]*)
                ai "Opted out. ODS will download and run the local llama-server."
                ;;
            *)
                export EXTERNAL_LLM_URL="${api_url}"
                export EXTERNAL_LLM_PROVIDER="${provider}"
                export EXTERNAL_LLM_MODEL="${matched_model}"
                export SKIP_MODEL_DOWNLOAD="true"
                ai_ok "Configured ODS to reuse $provider ($matched_model)"
                resolve_compose_config
                ;;
        esac
    else
        # Non-interactive mode with matches: reuse by default to be resource-efficient
        export EXTERNAL_LLM_URL="${api_url}"
        export EXTERNAL_LLM_PROVIDER="${provider}"
        export EXTERNAL_LLM_MODEL="${matched_model}"
        export SKIP_MODEL_DOWNLOAD="true"
        log "Non-interactive install: auto-reusing running external $provider service ($matched_model)"
        resolve_compose_config
    fi
else
    log "No active Ollama or LM Studio service with a matching model (${GGUF_FILE:-}) was found."
fi
