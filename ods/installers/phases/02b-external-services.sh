#!/bin/bash
# Detect or validate an explicitly selected host Ollama / LM Studio runtime.

# Isolated phase reuse (tests) gets the route predicate installers/lib/
# native-llm.sh gives install-core: a host-native llama-server is in use.
declare -F ods_native_llm_requested >/dev/null 2>&1 \
    || ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }

ods_progress 15 "detection" "Checking external LLM services"

_external_disable="${EXTERNAL_LLM_DISABLE:-false}"
_external_url="${EXTERNAL_LLM_URL:-}"
_external_provider="${EXTERNAL_LLM_PROVIDER:-}"
_external_model="${EXTERNAL_LLM_MODEL:-}"
_previous_external_url=""
[[ -f "${INSTALL_DIR:-}/.env" ]] && _previous_external_url="$(external_llm_env_value "$INSTALL_DIR/.env" EXTERNAL_LLM_URL || true)"
EXTERNAL_LLM_API_KEY_RESET=false
[[ "${EXTERNAL_LLM_API_KEY_DISABLE:-false}" != "true" ]] || EXTERNAL_LLM_API_KEY_RESET=true
if [[ -n "$_external_url" && "$(external_llm_strip_url "$_external_url")" != "$(external_llm_strip_url "$_previous_external_url")" ]]; then
    # A new endpoint must never inherit a credential from the old endpoint.
    EXTERNAL_LLM_API_KEY_RESET=true
fi
# A key passed for this run (--external-llm-key-env) replaces the stored one.
if [[ -z "${EXTERNAL_LLM_API_KEY_FILE:-}" && -z "${EXTERNAL_LLM_API_KEY_VALUE:-}" && "$EXTERNAL_LLM_API_KEY_RESET" != "true" && -s "${INSTALL_DIR:-}/config/litellm/external-upstream.key" ]]; then
    EXTERNAL_LLM_API_KEY_FILE="$INSTALL_DIR/config/litellm/external-upstream.key"
fi
unset _previous_external_url
if [[ "$_external_disable" != "true" && -n "${EXTERNAL_LLM_API_KEY_FILE:-}" ]] &&
   ! external_llm_read_api_key "$EXTERNAL_LLM_API_KEY_FILE" >/dev/null; then
    ai_bad "External LLM key file failed private-file validation."
    return 1
fi

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

if [[ -z "$_external_url" && "${ODS_MODE:-local}" == "local" ]] && ! ods_native_llm_requested; then
    _detected_provider=""
    _detected_url=""
    _detected_model=""
    _ods_detect_external_candidate() {
        local _candidate _candidate_provider _candidate_url _candidate_model
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
    }
    _ods_detect_external_candidate
    unset -f _ods_detect_external_candidate

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
    if [[ -n "${EXTERNAL_LLM_API_KEY_FILE:-}" && "${_external_disable}" != "true" ]]; then
        ai_bad "An external LLM key file requires an external model endpoint."
        return 1
    fi
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
if ods_native_llm_requested; then
    ai_bad "External Ollama / LM Studio reuse cannot be combined with the host-native llama-server (--native-llm-url)."
    ai "Select one host-managed inference backend, or use --no-external-llm."
    return 1
fi
if ! external_llm_validate_url "$_external_url"; then
    ai_bad "Invalid external LLM URL: ${_external_url}"
    ai "Use an http(s) base URL without credentials, query parameters, or fragments."
    return 1
fi

# A selected API that fails discovery is usually unreachable or needs a
# (current) API key; say which before blaming the provider or the model.
# Returns 1 when the model list answered, so the caller's message applies.
_external_llm_explain_failure() {
    local diagnosis
    diagnosis="$(external_llm_diagnose "$1" "$2")"
    case "$diagnosis" in
        unreachable)
            ai_bad "Could not reach ${2}."
            ai "Check the address, and that the service is running and reachable from this computer."
            ;;
        key-required)
            ai_bad "${2} needs an API key."
            # Windows setup always passes its System32 path; its owner runs
            # install.ps1, where chmod means nothing (fleet row 30).
            if [[ -n "${ODS_WINDOWS_SYSTEM_DIRECTORY:-}" ]]; then
                ai "Save the key as one line in a text file only you can read, then rerun with -ExternalLlmKeyFile FILE."
            else
                ai "Save the key as one line in a private file (chmod 600), then rerun with --external-llm-key-file FILE."
            fi
            ;;
        key-refused)
            ai_bad "${2} refused the API key."
            if [[ -n "${ODS_WINDOWS_SYSTEM_DIRECTORY:-}" ]]; then
                ai "Check that the key is current for this server, then rerun with it: -ExternalLlmKeyFile FILE."
            else
                ai "Check that the key is current for this server, then rerun with it: --external-llm-key-file FILE."
            fi
            ;;
        http-429)
            ai_bad "${2} is rate-limiting requests (HTTP 429)."
            ai "Wait a minute, then rerun the installer."
            ;;
        http-*)
            ai_bad "${2} answered HTTP ${diagnosis#http-} instead of a model list."
            ai "Check that this is the API base address (the part before /v1) and that the service is up."
            ;;
        *) return 1 ;;
    esac
}

_external_url="$(external_llm_strip_url "$_external_url")"
if [[ -z "$_external_provider" || "$_external_provider" == "auto" ]]; then
    _external_provider="$(external_llm_detect_provider "$_external_url" || true)"
    if [[ -z "$_external_provider" ]] && _external_llm_explain_failure openai-compatible "$_external_url"; then
        return 1
    fi
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
    _external_llm_explain_failure "$_external_provider" "$_external_url" && return 1
    if [[ -n "$_external_model" ]]; then
        _external_served=""
        while IFS= read -r _external_served_model; do
            _external_served="${_external_served:+${_external_served}, }${_external_served_model}"
        done < <(external_llm_models "$_external_provider" "$_external_url" 2>/dev/null | head -n 8)
        ai_bad "${_external_url} does not serve the model ${_external_model}."
        [[ -z "$_external_served" ]] || ai "Models it lists include: ${_external_served}"
        ai "Rerun with one of those names: --external-llm-model MODEL (Windows: -ExternalLlmModel MODEL)."
        unset _external_served _external_served_model
        return 1
    fi
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
