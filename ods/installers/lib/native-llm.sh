#!/bin/bash
# ============================================================================
# ODS Installer — host-native llama-server route
# ============================================================================
# Part of: installers/lib/
# Purpose: Pure helpers for an ODS-managed llama-server that runs outside the
#          Docker stack. The Windows Portal runs llama-server.exe as an owned
#          per-user task while this stack runs in WSL, and passes its origin
#          with --native-llm-url.
#
# Expects: nothing (pure functions)
# Provides: ods_native_llm_requested(), ods_native_llm_normalize_origin(),
#           ods_native_llm_container_origin(), ods_native_llm_origin_port()
#
# Modder notes:
#   NATIVE_LLM_BASE_URL is the origin seen from the host (loopback);
#   NATIVE_LLM_CONTAINER_BASE_URL is the same server seen from containers
#   (host.docker.internal). Neither carries a path: clients append /v1.
# ============================================================================

# True when this install uses a host-native llama-server.
ods_native_llm_requested() {
    [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]
}

# Print an http origin without a trailing slash or /v1 (/api/v1 from the
# retired Lemonade flags is accepted too). Returns 1 for anything else: a
# query, credentials, another path or a non-numeric port.
ods_native_llm_normalize_origin() {
    local url="${1:-}"
    url="${url%/}"
    case "$url" in
        */api/v1) url="${url%/api/v1}" ;;
        */v1) url="${url%/v1}" ;;
    esac
    [[ "$url" =~ ^http://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):([0-9]{1,5})$ ]] || return 1
    (( 10#${BASH_REMATCH[2]} >= 1 && 10#${BASH_REMATCH[2]} <= 65535 )) || return 1
    printf '%s\n' "$url"
}

# Print the origin containers use for a host loopback origin. Other hosts are
# already reachable from containers and are printed unchanged.
ods_native_llm_container_origin() {
    local origin
    origin="$(ods_native_llm_normalize_origin "${1:-}")" || return 1
    case "$origin" in
        http://localhost:*) printf '%s\n' "http://host.docker.internal:${origin##*:}" ;;
        http://127.0.0.1:*) printf '%s\n' "http://host.docker.internal:${origin##*:}" ;;
        "http://[::1]:"*) printf '%s\n' "http://host.docker.internal:${origin##*:}" ;;
        *) printf '%s\n' "$origin" ;;
    esac
}

# Print the port of an origin.
ods_native_llm_origin_port() {
    local origin
    origin="$(ods_native_llm_normalize_origin "${1:-}")" || return 1
    printf '%s\n' "$((10#${origin##*:}))"
}
