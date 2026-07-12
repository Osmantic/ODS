#!/usr/bin/env bash
# ============================================================================
# ODS — shared auth + port resolution for test scripts
# ============================================================================
# Source this from any test script that hits the dashboard-api. Provides:
#
#   ae_resolve       Populate DASHBOARD_API_KEY, DASHBOARD_API_PORT, and
#                    SERVICE_HOST from (in order) shell env → installer .env.
#                    Contract: shell env wins over .env. Scrubs CRLF, quotes,
#                    inline `# comments`, and trailing whitespace on every
#                    .env-sourced value.
#
#   ae_key_available Returns 0 when DASHBOARD_API_KEY is non-empty, 1 otherwise.
#
#   AE_AUTH_HEADER   Array populated by ae_resolve — either
#                    (-H "Authorization: Bearer <key>") when auth is
#                    available, or () when not. Only splat as
#                    "${AE_AUTH_HEADER[@]}" INSIDE an `if ae_key_available`
#                    guard; empty-array expansion under `set -u` errors on
#                    bash 3.2 (macOS default).
#
#   ae_env_file      Absolute path to the .env file being read (or "" if none).
#   ae_api_base      Composed base URL: http://$SERVICE_HOST:$DASHBOARD_API_PORT
#
# Env inputs (any of these can override the .env value at any time):
#   ODS_INSTALL_DIR      Where to look for .env (defaults to script's ../).
#   DASHBOARD_API_KEY    Bearer token; shell value wins.
#   DASHBOARD_API_PORT   API port; shell value wins.
#   SERVICE_HOST         Bind host; defaults to 127.0.0.1.
#
# All helpers are read-only and safe under `set -uo pipefail`.
# ============================================================================

# _ae_clean_env_value: normalize a raw .env value — strip trailing CR (Windows
# / WSL cross-usage), surrounding quotes, any inline `# comment`, and trailing
# whitespace. Bash 3.2 compatible.
_ae_clean_env_value() {
    local v="$1"
    v="${v%$'\r'}"
    v="${v#\"}"; v="${v%\"}"
    v="${v#\'}"; v="${v%\'}"
    v="${v%%#*}"
    while [[ "$v" == *[[:space:]] ]]; do v="${v%[[:space:]]}"; done
    printf '%s' "$v"
}

# _ae_read_env: grep + scrub a single KEY from a .env file. Prints value or "".
_ae_read_env() {
    local key="$1" file="$2"
    [[ -f "$file" ]] || return 0
    local raw
    raw="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-)"
    _ae_clean_env_value "$raw"
}

# ae_resolve: populate DASHBOARD_API_KEY, DASHBOARD_API_PORT, SERVICE_HOST,
# ae_env_file, ae_api_base. Idempotent — safe to call more than once.
ae_resolve() {
    local caller_dir="${1:-}"
    if [[ -z "$caller_dir" ]]; then
        # Assume caller sourced us; use their SCRIPT_DIR if present, else pwd.
        caller_dir="${SCRIPT_DIR:-$PWD}"
    fi
    local root="${ODS_INSTALL_DIR:-$(cd "$caller_dir/.." 2>/dev/null && pwd)}"
    [[ -z "$root" ]] && root="$caller_dir"
    ae_env_file="$root/.env"

    # Key: shell env wins; else read from .env (scrubbed).
    local key="${DASHBOARD_API_KEY:-}"
    if [[ -z "$key" ]]; then
        key="$(_ae_read_env DASHBOARD_API_KEY "$ae_env_file")"
    fi
    # One last CR strip in case the value came from a pre-loaded environment
    # where load_env_file / another loader didn't scrub trailing CR.
    key="${key%$'\r'}"
    DASHBOARD_API_KEY="$key"

    # Port: shell env wins; else .env; else default 3002.
    local port="${DASHBOARD_API_PORT:-}"
    if [[ -z "$port" ]]; then
        port="$(_ae_read_env DASHBOARD_API_PORT "$ae_env_file")"
    fi
    port="${port%$'\r'}"
    DASHBOARD_API_PORT="${port:-3002}"

    # Bind host.
    SERVICE_HOST="${SERVICE_HOST:-127.0.0.1}"
    SERVICE_HOST="${SERVICE_HOST%$'\r'}"

    ae_api_base="http://${SERVICE_HOST}:${DASHBOARD_API_PORT}"

    # Build the Bearer-header array for callers to splat into `curl (...)`.
    # Kept as an array so single-arg forms like -H "Authorization: Bearer key"
    # survive word-splitting cleanly even when the key contains punctuation.
    if [[ -n "${DASHBOARD_API_KEY:-}" ]]; then
        AE_AUTH_HEADER=(-H "Authorization: Bearer ${DASHBOARD_API_KEY}")
    else
        AE_AUTH_HEADER=()
    fi
}

# ae_key_available: 0 if DASHBOARD_API_KEY is set + non-empty, 1 otherwise.
ae_key_available() {
    [[ -n "${DASHBOARD_API_KEY:-}" ]]
}
