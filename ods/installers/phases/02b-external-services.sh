#!/bin/bash
# Detect or validate an explicitly selected host Ollama / LM Studio runtime.

ods_progress 15 "detection" "Checking external LLM services"

_external_disable="${EXTERNAL_LLM_DISABLE:-false}"
_external_url="${EXTERNAL_LLM_URL:-}"
_external_provider="${EXTERNAL_LLM_PROVIDER:-}"
_external_model="${EXTERNAL_LLM_MODEL:-}"

if [[ "$_external_disable" != "true" && -z "$_external_url" && -f "${INSTALL_DIR:-}/.env" ]]; then
    _external_url="$(external_llm_env_value "$INSTALL_DIR/.env" EXTERNAL_LLM_URL || true)"
    _external_provider="$(external_llm_env_value "$INSTALL_DIR/.env" EXTERNAL_LLM_PROVIDER || true)"
    _external_model="$(external_llm_env_value "$INSTALL_DIR/.env" EXTERNAL_LLM_MODEL || true)"
    if [[ -n "$_external_url" ]]; then
        log "Reusing the external LLM selection from the existing installation"
    fi
fi

if [[ "$_external_disable" == "true" ]]; then
    EXTERNAL_LLM_URL=""
    EXTERNAL_LLM_CONTAINER_URL=""
    EXTERNAL_LLM_PROVIDER=""
    EXTERNAL_LLM_MODEL=""
    SKIP_MODEL_DOWNLOAD=false
    EXTERNAL_LLM_RESET=true
    export EXTERNAL_LLM_URL EXTERNAL_LLM_CONTAINER_URL EXTERNAL_LLM_PROVIDER
    export EXTERNAL_LLM_MODEL SKIP_MODEL_DOWNLOAD EXTERNAL_LLM_RESET
    log "External LLM reuse disabled explicitly"
    return 0
fi

if [[ -z "$_external_url" && "${ODS_MODE:-local}" == "local" && "${LEMONADE_EXTERNAL:-false}" != "true" ]]; then
    _detected_provider=""
    _detected_url=""
    _detected_model=""
    for _candidate in "ollama|http://127.0.0.1:11434" "lmstudio|http://127.0.0.1:1234"; do
        _candidate_provider="${_candidate%%|*}"
        _candidate_url="${_candidate#*|}"
        _candidate_model="$(external_llm_resolve_model \
            "$_candidate_provider" "$_candidate_url" "" "${GGUF_FILE:-${LLM_MODEL:-}}" || true)"
        if [[ -n "$_candidate_model" ]]; then
            _detected_provider="$_candidate_provider"
            _detected_url="$_candidate_url"
            _detected_model="$_candidate_model"
            break
        fi
    done

    if [[ -n "$_detected_model" ]]; then
        if [[ "${INTERACTIVE:-false}" == "true" && "${DRY_RUN:-false}" != "true" ]]; then
            ai_ok "Found ${_detected_model} in the running ${_detected_provider} service"
            ai "ODS can reuse it and skip the duplicate GGUF download."
            read -r -p "  Reuse this external model service? [Y/n] " _external_reply < /dev/tty
            if [[ ! "$_external_reply" =~ ^[Nn] ]]; then
                _external_url="$_detected_url"
                _external_provider="$_detected_provider"
                _external_model="$_detected_model"
            fi
        elif [[ "${EXTERNAL_LLM_AUTO_REUSE:-false}" == "true" ]]; then
            _external_url="$_detected_url"
            _external_provider="$_detected_provider"
            _external_model="$_detected_model"
            log "Explicit auto-reuse selected ${_detected_provider} model ${_detected_model}"
        else
            log "Matching ${_detected_provider} model detected but not reused in non-interactive mode without --reuse-external-llm"
        fi
    fi
fi

if [[ -z "$_external_url" ]]; then
    EXTERNAL_LLM_URL=""
    EXTERNAL_LLM_CONTAINER_URL=""
    EXTERNAL_LLM_PROVIDER=""
    EXTERNAL_LLM_MODEL=""
    SKIP_MODEL_DOWNLOAD=false
    export EXTERNAL_LLM_URL EXTERNAL_LLM_CONTAINER_URL EXTERNAL_LLM_PROVIDER
    export EXTERNAL_LLM_MODEL SKIP_MODEL_DOWNLOAD
    return 0
fi

if [[ "${ODS_MODE:-local}" != "local" ]]; then
    ai_bad "External Ollama / LM Studio reuse is only supported in local mode."
    ai "Use --no-external-llm before selecting cloud or hybrid mode."
    return 1
fi
if [[ "${LEMONADE_EXTERNAL:-false}" == "true" ]]; then
    ai_bad "External Ollama / LM Studio reuse cannot be combined with external Lemonade."
    ai "Select one host-managed inference backend, or use --no-external-llm."
    return 1
fi
if ! external_llm_validate_url "$_external_url"; then
    ai_bad "Invalid external LLM URL: ${_external_url}"
    ai "Use an http(s) base URL without credentials, query parameters, or fragments."
    return 1
fi

_external_url="$(external_llm_strip_url "$_external_url")"
if [[ -z "$_external_provider" || "$_external_provider" == "auto" ]]; then
    _external_provider="$(external_llm_detect_provider "$_external_url" || true)"
fi
case "$_external_provider" in
    ollama|lmstudio|openai-compatible) ;;
    *)
        ai_bad "Could not identify the external LLM provider at ${_external_url}"
        ai "Use --external-llm-provider ollama|lmstudio|openai-compatible and verify the service is running."
        return 1
        ;;
esac

_resolved_external_model="$(external_llm_resolve_model \
    "$_external_provider" "$_external_url" "$_external_model" "${GGUF_FILE:-${LLM_MODEL:-}}" || true)"
if [[ -z "$_resolved_external_model" ]]; then
    ai_bad "The selected external ${_external_provider} service does not expose the required model."
    ai "Expected a model matching ${GGUF_FILE:-${LLM_MODEL:-unknown}}."
    ai "Use --external-llm-model MODEL to select an exact model exposed by the service."
    return 1
fi

EXTERNAL_LLM_URL="$_external_url"
EXTERNAL_LLM_CONTAINER_URL="$(external_llm_container_url "$_external_url")"
EXTERNAL_LLM_PROVIDER="$_external_provider"
EXTERNAL_LLM_MODEL="$_resolved_external_model"
SKIP_MODEL_DOWNLOAD=true
export EXTERNAL_LLM_URL EXTERNAL_LLM_CONTAINER_URL EXTERNAL_LLM_PROVIDER
export EXTERNAL_LLM_MODEL SKIP_MODEL_DOWNLOAD

ai_ok "Using external ${EXTERNAL_LLM_PROVIDER} model ${EXTERNAL_LLM_MODEL}"
resolve_compose_config
