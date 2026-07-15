#!/bin/bash
# ============================================================================
# ODS Installer - Install Conflict Detection
# ============================================================================
# Part of: installers/lib/
# Purpose: Render the planned Compose stack and reject conflicting host or
#          Docker resource claims before installation mutates runtime state.
#
# Expects: SCRIPT_DIR, INSTALL_DIR, COMPOSE_FLAGS, DOCKER_CMD,
#          DOCKER_COMPOSE_CMD, DRY_RUN, GPU_BACKEND, GPU_COUNT, ODS_MODE,
#          BIND_ADDRESS, BIND_ADDRESS_EXPLICIT, INSTALL_CONFLICT_REPORT_FILE,
#          log(), ai(), ai_warn()
# Optional overrides: INSTALL_CONFLICT_SOURCE_DIR,
#          INSTALL_CONFLICT_INSTALL_DIR, INSTALL_CONFLICT_COMPOSE_FLAGS,
#          INSTALL_CONFLICT_ENV_FILE, INSTALL_CONFLICT_UPDATE_MODE,
#          INSTALL_CONFLICT_DETECTOR
# Provides: ods_install_env_value(), ods_install_port_defaults(),
#           ods_run_install_conflict_check()
# ============================================================================

ods_install_conflicts_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

ods_install_env_value() {
    local env_file="$1"
    local key="$2"
    local default="${3:-}"
    local value=""

    if [[ -f "$env_file" ]]; then
        value="$(
            awk -v target="$key" '
                {
                    line = $0
                    sub(/\r$/, "", line)
                    if (line ~ /^[[:space:]]*(#|$)/) {
                        next
                    }
                    sub(/^[[:space:]]*export[[:space:]]+/, "", line)
                    separator = index(line, "=")
                    if (separator == 0) {
                        next
                    }
                    candidate = substr(line, 1, separator - 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate)
                    if (candidate != target) {
                        next
                    }
                    value = substr(line, separator + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                    if (length(value) >= 2) {
                        first = substr(value, 1, 1)
                        last = substr(value, length(value), 1)
                        if ((first == "\"" || first == "\047") && first == last) {
                            value = substr(value, 2, length(value) - 2)
                        }
                    }
                    print value
                    exit
                }
            ' "$env_file"
        )"
    fi

    if [[ -n "$value" ]]; then
        printf '%s\n' "$value"
    else
        printf '%s\n' "$default"
    fi
}

ods_install_port_defaults() {
    cat <<'PORT_DEFAULTS'
OLLAMA_PORT=11434
WEBUI_PORT=3000
SEARXNG_PORT=8888
PERPLEXICA_PORT=3004
WHISPER_PORT=9000
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
EMBEDDINGS_PORT=8090
LITELLM_PORT=4000
OPENCLAW_PORT=7860
SHIELD_PORT=8085
DASHBOARD_API_PORT=3002
DASHBOARD_PORT=3001
COMFYUI_PORT=8188
TOKEN_SPY_PORT=3005
LANGFUSE_PORT=3006
APE_PORT=7890
BRAVE_SEARCH_PORT=8585
HERMES_PROXY_PORT=9120
ODS_PROXY_PORT=80
ODS_PROXY_TLS_PORT=443
OPENCODE_PORT=3003
ODS_AGENT_PORT=7710
PORT_DEFAULTS
}

ods_run_install_conflict_check() {
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log "[DRY RUN] Would render the selected Compose stack and check install conflicts"
        return 0
    fi

    local source_dir="${INSTALL_CONFLICT_SOURCE_DIR:-$SCRIPT_DIR}"
    local install_dir="${INSTALL_CONFLICT_INSTALL_DIR:-$INSTALL_DIR}"
    local compose_flags="${INSTALL_CONFLICT_COMPOSE_FLAGS:-${COMPOSE_FLAGS:-}}"
    local report_file="${INSTALL_CONFLICT_REPORT_FILE:-/tmp/ods-install-conflicts.json}"
    local detector="${INSTALL_CONFLICT_DETECTOR:-$source_dir/scripts/check-install-conflicts.py}"
    if [[ ! -f "$detector" ]]; then
        ai_warn "Install conflict detector is missing: $detector"
        return 2
    fi

    local python_cmd="${ODS_PYTHON_CMD:-}"
    if [[ -z "$python_cmd" ]] && declare -f ods_detect_python_cmd >/dev/null 2>&1; then
        python_cmd="$(ods_detect_python_cmd 2>/dev/null || true)"
    fi
    if [[ -z "$python_cmd" ]]; then
        if command -v python3 >/dev/null 2>&1; then
            python_cmd="python3"
        elif command -v python >/dev/null 2>&1; then
            python_cmd="python"
        else
            ai_warn "Python is required for install conflict detection"
            return 2
        fi
    fi

    local env_file="${INSTALL_CONFLICT_ENV_FILE:-}"
    local update_mode="false"
    if [[ -n "${INSTALL_CONFLICT_UPDATE_MODE:-}" ]]; then
        if ods_install_conflicts_truthy "$INSTALL_CONFLICT_UPDATE_MODE"; then
            update_mode="true"
        fi
    elif [[ -f "$install_dir/.env" ]]; then
        update_mode="true"
    fi
    if [[ -z "$env_file" ]]; then
        if [[ "$update_mode" == "true" ]]; then
            env_file="$install_dir/.env"
        else
            env_file="$source_dir/.env.example"
        fi
    fi
    if [[ ! -f "$env_file" ]]; then
        ai_warn "Compose environment template is missing: $env_file"
        return 2
    fi

    local bind_address="${BIND_ADDRESS:-127.0.0.1}"
    if [[ "${BIND_ADDRESS_EXPLICIT:-false}" != "true" ]]; then
        bind_address="$(
            ods_install_env_value "$env_file" BIND_ADDRESS "$bind_address"
        )"
    fi

    local effective_mode="${ODS_MODE:-local}"
    if ods_install_conflicts_truthy "${LEMONADE_EXTERNAL:-false}" \
        || { [[ "${GPU_BACKEND:-}" == "amd" ]] && [[ "$effective_mode" == "local" ]]; }
    then
        effective_mode="lemonade"
    fi

    local -a detector_args=(
        --source-dir "$source_dir"
        --install-dir "$install_dir"
        --compose-command "${DOCKER_COMPOSE_CMD:-docker compose}"
        --compose-flags "$compose_flags"
        --docker-command "${DOCKER_CMD:-docker}"
        --env-file "$env_file"
        --env-override "BIND_ADDRESS=$bind_address"
        --env-override "ODS_MODE=$effective_mode"
        --env-override "GPU_BACKEND=${GPU_BACKEND:-cpu}"
        --env-override "GPU_COUNT=${GPU_COUNT:-0}"
        --env-override "LEMONADE_EXTERNAL=${LEMONADE_EXTERNAL:-false}"
        --report "$report_file"
    )

    local port_key port_default port_value
    while IFS='=' read -r port_key port_default; do
        [[ -n "$port_key" ]] || continue
        port_value="${!port_key-}"
        if [[ -z "$port_value" ]]; then
            port_value="$(ods_install_env_value "$env_file" "$port_key" "$port_default")"
        fi
        detector_args+=(--env-override "$port_key=$port_value")
    done < <(ods_install_port_defaults)

    local ods_proxy_bind="${ODS_PROXY_BIND:-}"
    if [[ -z "$ods_proxy_bind" ]]; then
        ods_proxy_bind="$(ods_install_env_value "$env_file" ODS_PROXY_BIND "0.0.0.0")"
    fi
    detector_args+=(--env-override "ODS_PROXY_BIND=$ods_proxy_bind")

    if [[ "$update_mode" == "true" ]]; then
        detector_args+=(--update)
    fi

    if declare -f ods_progress >/dev/null 2>&1; then
        ods_progress 35 "docker" "Checking install conflicts"
    fi
    ai "Checking planned ports and Docker resource ownership..."

    local detector_status=0
    if "$python_cmd" "$detector" "${detector_args[@]}"; then
        return 0
    else
        detector_status=$?
    fi

    case "$detector_status" in
        1)
            if ods_install_conflicts_truthy "${ODS_ALLOW_CONFLICTS:-}"; then
                ai_warn "Install conflicts were explicitly accepted with ODS_ALLOW_CONFLICTS=1."
                ai_warn "The installer will not isolate ports or data automatically."
                return 0
            fi
            return 1
            ;;
        2)
            return 2
            ;;
        *)
            ai_warn "Install conflict detector exited unexpectedly with status $detector_status"
            return 2
            ;;
    esac
}
