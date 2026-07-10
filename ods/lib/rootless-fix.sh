#!/bin/bash
# =============================================================================
# ODS — lib/rootless-fix.sh
# =============================================================================
# Helpers for detecting Docker rootless mode and correcting two classes of
# problems that affect rootless Docker installs:
#
# 1. Data-directory ownership
#    In Docker rootless mode the UID namespace is shifted by the host user's
#    subuid offset (typically 100000).  Containers that run as UID 0 map to
#    the host user (fine), but containers that run as a non-root UID N map to
#    host UID (100000 + N - 1).  The installer creates data directories owned
#    by the host user (UID 1000), so those non-root containers hit EACCES.
#
# 2. Host-agent network reachability
#    In rootless mode, `host.docker.internal` is NOT automatically registered
#    in the container's /etc/hosts (unlike Docker Desktop / rootful mode).
#    dashboard-api inside the compose network cannot reach the ODS host agent
#    unless:
#      a) ODS_AGENT_BIND=0.0.0.0  (agent listens on all interfaces, not just
#         the Docker gateway)
#      b) ODS_AGENT_HOST=<LAN IP>  (containers address the agent via the host's
#         LAN IP, which IS routable from the compose network)
#
# Public API
# ----------
#   ods_is_rootless_docker          → exit 0 if rootless, exit 1 otherwise
#   ods_fix_rootless_ownership      → chown all affected dirs; idempotent/safe
#   ods_fix_rootless_agent_network  → set ODS_AGENT_BIND + ODS_AGENT_HOST in
#                                     .env; idempotent/safe
#   ods_warn_rootless_docker        → print a human-readable warning block
#
# Caller requirements
# -------------------
#   INSTALL_DIR must be set before calling ods_fix_rootless_ownership or
#   ods_fix_rootless_agent_network.
#   docker must be in PATH.
#   The functions are deliberately side-effect-free when rootless is not
#   detected; they are always safe to call unconditionally.
# =============================================================================

# Sourced from the compose files and entrypoint scripts for each service.
#
# Services NOT listed here run as UID 0 (root) in the container; those map
# to the host user in rootless mode and need no special handling.
_ods_rootless_get_uid() {
    case "$1" in
        n8n|whisper|tts|token-spy|privacy-shield|openclaw)
            printf '1000'
            ;;
        ape)
            printf '100'
            ;;
        hermes)
            printf '10000'
            ;;
    esac
}

# ---------------------------------------------------------------------------
# ods_is_rootless_docker
# Returns 0 if Docker is running in rootless mode, 1 otherwise.
# ---------------------------------------------------------------------------
ods_is_rootless_docker() {
    docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless
}

# ---------------------------------------------------------------------------
# ods_warn_rootless_docker [INSTALL_DIR]
# Print a human-readable advisory block.  Safe to call regardless of mode.
# ---------------------------------------------------------------------------
ods_warn_rootless_docker() {
    local install_dir="${1:-${INSTALL_DIR:-~/ods}}"
    cat <<ROOTLESS_WARN
[!!] Docker rootless mode detected.
     In rootless mode, container UIDs are remapped through the host user's
     subuid offset (typically 100000).  Non-root container users such as
     node (UID 1000), hermes (UID 10000), and nextjs (UID 1001) will be
     remapped to host UIDs 100999, 109999, 101000, etc.  Data directories
     created by the installer are owned by the host user and cannot be
     written by those remapped UIDs.

     ODS will automatically fix ownership for all affected services.
     If you see EACCES errors after a manual reinstall, run:
       ods repair rootless-ownership

ROOTLESS_WARN
}

# ---------------------------------------------------------------------------
# _ods_rootless_chown_dir SERVICE_DATA_DIR CONTAINER_UID
# Internal helper — runs a short-lived Alpine container to chown one dir.
# ---------------------------------------------------------------------------
_ods_rootless_chown_dir() {
    local dir="$1"
    local uid="$2"

    [[ -d "$dir" ]] || return 0          # nothing to fix
    [[ -n "$uid" ]] || return 0

    # Use UID 0 in the container (= host user in rootless mode) so we have
    # permission to chown without sudo.
    docker run --rm \
        --user 0:0 \
        --network none \
        -v "${dir}:/data" \
        alpine:3 \
        sh -c "chown -R ${uid}:${uid} /data" \
        2>/dev/null || {
            echo "[warn] rootless-fix: chown ${uid}:${uid} on ${dir} failed (non-fatal)" >&2
            return 0   # non-fatal; continuing is better than hard-failing
        }
}

# ---------------------------------------------------------------------------
# ods_fix_rootless_ownership [INSTALL_DIR]
# Idempotent — sets ownership on all affected data dirs for rootless Docker.
# Only does anything when rootless mode is actually detected.
# ---------------------------------------------------------------------------
ods_fix_rootless_ownership() {
    local install_dir="${1:-${INSTALL_DIR:-}}"

    if [[ -z "$install_dir" ]]; then
        echo "[warn] rootless-fix: INSTALL_DIR not set — skipping ownership fix" >&2
        return 0
    fi

    if ! ods_is_rootless_docker; then
        return 0    # not rootless — nothing to do
    fi

    ods_warn_rootless_docker "$install_dir"
    echo "[ods] Fixing data-directory ownership for Docker rootless mode..."

    local svc uid
    local services=(n8n whisper tts token-spy privacy-shield ape hermes openclaw)
    for svc in "${services[@]}"; do
        uid=$(_ods_rootless_get_uid "$svc")
        local target_dir="${install_dir}/data/${svc}"
        if [[ -d "$target_dir" ]]; then
            echo "[ods]   chown -R ${uid}:${uid} data/${svc}"
            _ods_rootless_chown_dir "$target_dir" "$uid"
        fi
    done

    # Special case: langfuse database directories require specific UIDs
    # postgres (UID 70) and clickhouse (UID 101)
    local postgres_dir="${install_dir}/data/langfuse/postgres"
    if [[ -d "$postgres_dir" ]]; then
        echo "[ods]   chown -R 70:70 data/langfuse/postgres"
        _ods_rootless_chown_dir "$postgres_dir" "70"
    fi
    local clickhouse_dir="${install_dir}/data/langfuse/clickhouse"
    if [[ -d "$clickhouse_dir" ]]; then
        echo "[ods]   chown -R 101:101 data/langfuse/clickhouse"
        _ods_rootless_chown_dir "$clickhouse_dir" "101"
    fi

    # Special case: openclaw workspace under config/ also needs UID 1000
    local openclaw_ws="${install_dir}/config/openclaw/workspace"
    if [[ -d "$openclaw_ws" ]]; then
        echo "[ods]   chown -R 1000:1000 config/openclaw/workspace"
        _ods_rootless_chown_dir "$openclaw_ws" "1000"
    fi

    echo "[ods] Rootless ownership fix complete."
}

# ---------------------------------------------------------------------------
# _ods_env_set KEY VALUE ENV_FILE
# Write/update a key=value pair in an .env file without sourcing it.
# Safe against values containing special characters.
# ---------------------------------------------------------------------------
_ods_env_set() {
    local key="$1" val="$2" file="${3:-${INSTALL_DIR}/.env}"
    [[ -f "$file" ]] || return 1
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        # Replace existing value using awk (avoids sed delimiter collisions)
        awk -v k="$key" -v v="$val" '{
            if (index($0, k "=") == 1) print k "=" v; else print
        }' "$file" > "${file}.tmp" && cat "${file}.tmp" > "$file" && rm -f "${file}.tmp"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}

# ---------------------------------------------------------------------------
# _ods_env_get KEY ENV_FILE
# Read a key's value from .env without sourcing it.
# ---------------------------------------------------------------------------
_ods_env_get() {
    local key="$1" file="${2:-${INSTALL_DIR}/.env}"
    local val=""
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        val=$(grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi
    printf '%s' "$val"
}

# ---------------------------------------------------------------------------
# _ods_detect_lan_ip
# Print the first non-loopback, non-Docker IPv4 address, or empty string.
# ---------------------------------------------------------------------------
_ods_detect_lan_ip() {
    local ip=""
    # Prefer ip(8) with scope global (excludes loopback + link-local)
    if command -v ip >/dev/null 2>&1; then
        ip=$(ip -4 addr show scope global 2>/dev/null \
            | grep -oP 'inet \K[\d.]+' \
            | grep -v '^172\.\(1[6-9]\|2[0-9]\|3[01]\)\.' \
            | head -1)
    fi
    # Fallback: hostname -I (GNU), skip docker/loopback subnets
    if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1; then
        ip=$(hostname -I 2>/dev/null \
            | tr ' ' '\n' \
            | grep -v '^127\.' \
            | grep -v '^172\.\(1[6-9]\|2[0-9]\|3[01]\)\.' \
            | grep -v '^::' \
            | head -1)
    fi
    printf '%s' "$ip"
}

# ---------------------------------------------------------------------------
# ods_fix_rootless_agent_network [INSTALL_DIR]
# Ensure ODS_AGENT_BIND=0.0.0.0 and ODS_AGENT_HOST=<LAN IP> are set in
# .env for Docker rootless installs.
#
# Why both vars are needed in rootless mode:
#   • host.docker.internal is NOT registered in rootless containers
#     (it is only set by Docker Desktop / rootful daemon on Linux).
#   • The ODS host agent defaults to binding on the Docker network gateway
#     only, so containers on the bridge cannot reach it on the LAN IP.
#   • Setting BIND=0.0.0.0 makes the agent listen on all interfaces.
#   • Setting HOST=<LAN IP> tells dashboard-api which address to use.
#
# Idempotent: skips vars that already have the correct value.
# Only activates when rootless mode is detected.
# ---------------------------------------------------------------------------
ods_fix_rootless_agent_network() {
    local install_dir="${1:-${INSTALL_DIR:-}}"
    local env_file="${install_dir}/.env"

    if [[ -z "$install_dir" ]]; then
        echo "[warn] rootless-fix: INSTALL_DIR not set — skipping agent network fix" >&2
        return 0
    fi

    if ! ods_is_rootless_docker; then
        return 0    # not rootless — nothing to do
    fi

    if [[ ! -f "$env_file" ]]; then
        echo "[warn] rootless-fix: .env not found at $env_file — skipping agent network fix" >&2
        return 0
    fi

    echo "[ods] Configuring host-agent network for Docker rootless mode..."

    # ── ODS_AGENT_BIND ────────────────────────────────────────────────────────
    local current_bind
    current_bind=$(_ods_env_get "ODS_AGENT_BIND" "$env_file")
    if [[ "$current_bind" == "0.0.0.0" ]]; then
        echo "[ods]   ODS_AGENT_BIND already 0.0.0.0 — no change"
    else
        _ods_env_set "ODS_AGENT_BIND" "0.0.0.0" "$env_file"
        echo "[ods]   ODS_AGENT_BIND set to 0.0.0.0 (was: '${current_bind:-unset}')"
    fi

    # ── ODS_AGENT_HOST ────────────────────────────────────────────────────────
    local current_host
    current_host=$(_ods_env_get "ODS_AGENT_HOST" "$env_file")

    # If already set to a real IP (not host.docker.internal), leave it alone
    # so manual overrides are respected.
    if [[ -n "$current_host" && "$current_host" != "host.docker.internal" ]]; then
        echo "[ods]   ODS_AGENT_HOST already set to '$current_host' — no change"
        echo "[ods] Agent network fix complete."
        return 0
    fi

    local lan_ip
    lan_ip=$(_ods_detect_lan_ip)

    if [[ -z "$lan_ip" ]]; then
        echo "[warn] rootless-fix: could not auto-detect LAN IP for ODS_AGENT_HOST." >&2
        echo "[warn]   Set it manually: ods config edit  →  ODS_AGENT_HOST=<your VM/host IP>" >&2
        echo "[ods] Agent network fix complete (with warnings)."
        return 0
    fi

    _ods_env_set "ODS_AGENT_HOST" "$lan_ip" "$env_file"
    echo "[ods]   ODS_AGENT_HOST set to $lan_ip (was: '${current_host:-unset}')"
    echo "[ods]   (containers will now reach the host agent via $lan_ip)"
    echo "[ods] Agent network fix complete."
    echo "[ods] Run 'ods restart' to apply the new agent network config."
}
