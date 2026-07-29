#!/bin/bash
# ============================================================================
# ODS Tests — Shared Auth Environment Resolver
# ============================================================================
# Purpose: Load DASHBOARD_API_KEY and port vars from shell env (wins) or
#          .env file, strip CRLF/quotes/comments/whitespace, expose
#          AE_AUTH_HEADER array for curl splat.
#
# Bash 3.2 compatible (macOS default shell).
# ============================================================================

AE_AUTH_HEADER=()

_ae_read_env_var() {
    local key="$1"
    local default_home
    default_home="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    local env_file="${ODS_HOME:-$default_home}/.env"

    # Shell env wins
    if [ -n "${!key:-}" ]; then
        eval "echo \${$key}"
        return 0
    fi

    # Fall back to .env file
    if [ -f "$env_file" ]; then
        local val
        val=$(grep -m1 "^${key}=" "$env_file" 2>/dev/null | cut -d'=' -f2- | \
            tr -d '\r' | \
            sed "s/^['\"]//; s/['\"]$//" | \
            sed 's/[[:space:]]*#.*//' | \
            sed 's/[[:space:]]*$//')
        echo "$val"
        return 0
    fi

    return 1
}

_ae_load() {
    local key
    key=$(_ae_read_env_var "DASHBOARD_API_KEY") || key=""

    # Fall back to data/dashboard-api-key.txt if key not in env/.env
    if [ -z "$key" ]; then
        local default_home
        default_home="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
        local key_file="${ODS_HOME:-$default_home}/data/dashboard-api-key.txt"
        if [ -f "$key_file" ]; then
            key=$(cat "$key_file" | tr -d '\r\n ' || true)
        fi
    fi

    if [ -n "$key" ]; then
        AE_AUTH_HEADER=("-H" "Authorization: Bearer ${key}")
    else
        AE_AUTH_HEADER=()
    fi

    DASHBOARD_API_PORT=$(_ae_read_env_var "DASHBOARD_API_PORT") || \
        DASHBOARD_API_PORT="3002"
    DASHBOARD_PORT=$(_ae_read_env_var "DASHBOARD_PORT") || \
        DASHBOARD_PORT="3001"
    WHISPER_PORT=$(_ae_read_env_var "WHISPER_PORT") || \
        WHISPER_PORT="9000"
    TTS_PORT=$(_ae_read_env_var "TTS_PORT") || \
        TTS_PORT="8880"

    export AE_AUTH_HEADER DASHBOARD_API_PORT DASHBOARD_PORT \
           WHISPER_PORT TTS_PORT
}

_ae_require_key() {
    if [ ${#AE_AUTH_HEADER[@]} -eq 0 ]; then
        echo "[SKIP] DASHBOARD_API_KEY not set — auth-required checks skipped"
        echo "       Set DASHBOARD_API_KEY in shell env or \$ODS_HOME/.env to run these checks"
        return 1
    fi
    return 0
}

# Auto-load on source
_ae_load
