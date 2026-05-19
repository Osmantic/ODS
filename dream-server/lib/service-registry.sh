#!/bin/bash
# Service Registry — loads extension manifests and provides lookup functions.
# Source this file: . "$SCRIPT_DIR/lib/service-registry.sh"

EXTENSIONS_DIR="${SCRIPT_DIR:-$(pwd)}/extensions/services"
_SR_LOADED=false
_SR_FAILED=false
_SR_CACHE="/tmp/dream-service-registry.$$.sh"

# Caching for compose flags (session-level)
_SR_COMPOSE_FLAGS_CACHE=""
_SR_COMPOSE_FLAGS_CACHED=false
_SR_CACHE_HITS=0
_SR_CACHE_MISSES=0

# Bash 4+ required for associative arrays used throughout the service registry.
# macOS ships Bash 3.2 — users must install a modern shell (e.g. via Homebrew).
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: service-registry.sh requires Bash 4.0+ (current: $BASH_VERSION)" >&2
    echo "  macOS ships Bash 3.2 due to licensing. Install a modern version:" >&2
    echo "    brew install bash" >&2
    echo "  Then re-run with:  /opt/homebrew/bin/bash $0 \$*" >&2
    return 1 2>/dev/null || exit 1
fi

# Associative arrays (bash 4+)
declare -A SERVICE_ALIASES      # alias → service_id
declare -A SERVICE_CONTAINERS   # service_id → container_name
declare -A SERVICE_COMPOSE      # service_id → compose file path
declare -A SERVICE_CATEGORIES   # service_id → core|recommended|optional
declare -A SERVICE_DEPENDS      # service_id → space-separated dependency IDs
declare -A SERVICE_HEALTH       # service_id → health endpoint path
declare -A SERVICE_HEALTH_TIMEOUTS  # service_id → health check timeout in seconds
declare -A SERVICE_PORTS        # service_id → external port (what the user hits on localhost)
declare -A SERVICE_PORT_ENVS    # service_id → env var name for the external port
# Services with `host_network: true` in their manifest (Docker
# network_mode: host) — share the host's network namespace, so there's
# no Docker-mapped port. SERVICE_PORTS[sid] is irrelevant for them.
# Callers (tests, health probes, doctor) should skip the port check
# when SERVICE_HOST_NETWORK[sid] is "1".
declare -A SERVICE_HOST_NETWORK # service_id → "1" iff manifest sets host_network: true
declare -A SERVICE_NAMES        # service_id → display name
declare -A SERVICE_SETUP_HOOKS  # service_id → absolute path to setup script
declare -A SERVICE_GPU_BACKENDS # service_id → space-separated GPU backends (amd, nvidia, apple, cpu)
declare -a SERVICE_IDS          # ordered list of all service IDs

sr_load() {
    [[ "$_SR_LOADED" == "true" ]] && return 0
    SERVICE_IDS=()
    # Invalidate cache when reloading (extensions may have changed)
    _SR_COMPOSE_FLAGS_CACHED=false
    _SR_COMPOSE_FLAGS_CACHE=""

    # Single Python pass: reads ALL manifests, emits sourceable bash
    PYTHON_CMD="python3"
    if [[ -f "${SCRIPT_DIR:-$(pwd)}/lib/python-cmd.sh" ]]; then
        . "${SCRIPT_DIR:-$(pwd)}/lib/python-cmd.sh"
        PYTHON_CMD="$(ds_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    # Ensure PyYAML is available (Arch, Void, Alpine don't ship it by default).
    # install-core.sh initially loads the registry before CLI flags are parsed,
    # so avoid surprise sudo prompts until the installer has prepared sudo and
    # explicitly enabled DREAM_SR_AUTO_INSTALL_PYYAML.
    if ! "$PYTHON_CMD" -c "import yaml" 2>/dev/null; then
        if [[ "${DREAM_SR_AUTO_INSTALL_PYYAML:-}" == "1" ]] && declare -f pkg_install &>/dev/null && declare -f pkg_resolve &>/dev/null; then
            [[ -z "${PKG_MANAGER:-}" ]] && declare -f detect_pkg_manager &>/dev/null && detect_pkg_manager
            declare -f log &>/dev/null && log "PyYAML not found; installing system package..."
            # shellcheck disable=SC2046
            pkg_install $(pkg_resolve python3-pyyaml) 2>>"${LOG_FILE:-/dev/null}" || true
        fi
        if ! "$PYTHON_CMD" -c "import yaml" 2>/dev/null; then
            declare -f warn &>/dev/null && warn "PyYAML not available. Service registry will be incomplete."
            declare -f warn &>/dev/null && warn "Install manually: pip3 install pyyaml"
            _SR_LOADED=true  # Prevent repeated retries
            _SR_FAILED=true
            return 0
        fi
    fi

    if ! "$PYTHON_CMD" - "$EXTENSIONS_DIR" <<'PYEOF' > "$_SR_CACHE"
import yaml, sys, os
from pathlib import Path

import re as _re

_SAFE_VALUE = _re.compile(r'^[a-zA-Z0-9 _./:@,=-]*$')

def _esc(value):
    """Escape a value for safe inclusion in double-quoted bash assignment.
    Rejects values with characters that could enable shell injection."""
    s = str(value)
    if _SAFE_VALUE.match(s):
        return s
    # Strip characters that are dangerous in double-quoted bash strings
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`').replace('!', '\\!')

ext_dir = Path(sys.argv[1])
if not ext_dir.exists():
    sys.exit(0)

# Collect service dirs from both built-in and dashboard-installed extensions
_all_service_dirs = sorted(ext_dir.iterdir())
user_ext_dir = ext_dir.parent.parent / "data" / "user-extensions"
if user_ext_dir.exists():
    _all_service_dirs += sorted(user_ext_dir.iterdir())

_seen_ids = set()
for service_dir in _all_service_dirs:
    if not service_dir.is_dir():
        continue
    manifest_path = None
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = service_dir / name
        if candidate.exists():
            manifest_path = candidate
            break
    if not manifest_path:
        continue
    try:
        with open(manifest_path) as f:
            m = yaml.safe_load(f)
        if not isinstance(m, dict):
            print(f'# SKIP: {manifest_path}: not a valid YAML mapping', file=sys.stderr)
            continue
        if m.get("schema_version") != "dream.services.v1":
            continue
        s = m.get("service")
        if not isinstance(s, dict):
            print(f'# SKIP: {manifest_path}: missing or invalid "service" section', file=sys.stderr)
            continue
        sid = s.get("id", "")
        if not sid:
            print(f'# SKIP: {manifest_path}: missing required "id" field', file=sys.stderr)
            continue
        if sid in _seen_ids:
            print(f'# SKIP: {manifest_path}: duplicate service id {sid!r} (built-in takes precedence)', file=sys.stderr)
            continue
        _seen_ids.add(sid)

        # Validate service ID — must be safe for use as bash associative array key
        if not _re.match(r'^[a-zA-Z0-9_-]+$', sid):
            print(f'# SKIP: invalid service id: {sid!r}', file=sys.stderr)
            continue

        aliases = s.get("aliases", [])
        container = s.get("container_name", f"dream-{sid}")
        compose_file = s.get("compose_file", "")
        category = s.get("category", "optional")
        depends = s.get("depends_on", [])

        # Validate aliases
        valid_aliases = []
        for a in aliases:
            if _re.match(r'^[a-zA-Z0-9_-]+$', str(a)):
                valid_aliases.append(str(a))
            else:
                print(f'# SKIP alias: invalid alias {a!r} in {sid}', file=sys.stderr)

        # Resolve compose path (relative to extension dir)
        compose_path = ""
        if compose_file:
            full = service_dir / compose_file
            if full.exists():
                compose_path = str(full)

        # Emit sourceable lines — all values escaped for safe double-quoting
        print(f'SERVICE_IDS+=("{_esc(sid)}")')
        print(f'SERVICE_ALIASES["{_esc(sid)}"]="{_esc(sid)}"')
        for a in valid_aliases:
            print(f'SERVICE_ALIASES["{_esc(a)}"]="{_esc(sid)}"')
        print(f'SERVICE_CONTAINERS["{_esc(sid)}"]="{_esc(container)}"')
        print(f'SERVICE_COMPOSE["{_esc(sid)}"]="{_esc(compose_path)}"')
        print(f'SERVICE_CATEGORIES["{_esc(sid)}"]="{_esc(category)}"')
        print(f'SERVICE_DEPENDS["{_esc(sid)}"]="{_esc(" ".join(str(d) for d in depends))}"')
        health = s.get("health", "/health")
        health_timeout = s.get("health_timeout", 5)  # Default 5 seconds
        port = s.get("external_port_default", s.get("port", 0))
        port_env = s.get("external_port_env", "")
        host_network = "1" if s.get("host_network") else ""
        print(f'SERVICE_HEALTH["{_esc(sid)}"]="{_esc(health)}"')
        print(f'SERVICE_HEALTH_TIMEOUTS["{_esc(sid)}"]="{_esc(health_timeout)}"')
        print(f'SERVICE_PORTS["{_esc(sid)}"]="{_esc(port)}"')
        print(f'SERVICE_PORT_ENVS["{_esc(sid)}"]="{_esc(port_env)}"')
        if host_network:
            print(f'SERVICE_HOST_NETWORK["{_esc(sid)}"]="1"')
        print(f'SERVICE_NAMES["{_esc(sid)}"]="{_esc(s.get("name", sid))}"')
        # Prefer hooks.post_install over legacy setup_hook
        hooks = s.get("hooks", {})
        effective_hook = ""
        if isinstance(hooks, dict):
            effective_hook = hooks.get("post_install", "")
        if not effective_hook:
            effective_hook = s.get("setup_hook", "")
        setup_path = ""
        if effective_hook:
            full = service_dir / effective_hook
            if full.exists():
                setup_path = str(full)
        print(f'SERVICE_SETUP_HOOKS["{_esc(sid)}"]="{_esc(setup_path)}"')
        # GPU backends (default to amd/nvidia/apple, consistent with dashboard-api)
        gpu_backends = s.get("gpu_backends", ["amd", "nvidia", "apple"])
        backends_str = " ".join(str(b) for b in gpu_backends)
        print(f'SERVICE_GPU_BACKENDS["{_esc(sid)}"]="{_esc(backends_str)}"')
    except Exception as exc:
        print(f'# ERROR: failed to parse {manifest_path}: {exc}', file=sys.stderr)
        continue
PYEOF
    then
        rm -f "$_SR_CACHE"
        declare -f warn &>/dev/null && warn "Service registry: manifest parser failed"
        _SR_LOADED=true  # Prevent repeated retries
        _SR_FAILED=true
        return 0
    fi

    # Source the generated registry (one subprocess for all manifests)
    [[ -f "$_SR_CACHE" ]] && . "$_SR_CACHE"
    rm -f "$_SR_CACHE"
    _SR_LOADED=true
}

# Update SERVICE_PORTS with actual values from environment variables.
# Call AFTER sr_load + load_env_file so the env vars are populated.
# Uses SERVICE_PORT_ENVS (e.g. llama-server → OLLAMA_PORT) to resolve
# the env var name, then indirect expansion to get its value.
sr_resolve_ports() {
    for _sid in "${SERVICE_IDS[@]}"; do
        local _port_env="${SERVICE_PORT_ENVS[$_sid]:-}"
        if [[ -n "$_port_env" && -n "${!_port_env:-}" ]]; then
            SERVICE_PORTS[$_sid]="${!_port_env}"
        fi
    done

    # Lemonade (AMD) serves health at /api/v1/health, not /health
    if [[ "${GPU_BACKEND:-}" == "amd" ]]; then
        SERVICE_HEALTH[llama-server]="/api/v1/health"
    fi
}

# Resolve a user-provided name to a compose service ID.
#
# Users copy container names (e.g. `dream-token-spy`) from `docker ps` and
# expect them to work as arguments to `dream restart|stop|start|update`. The
# registry loader names every container `dream-<sid>` (or the manifest's
# explicit `container_name`, which by convention follows the same pattern),
# so stripping a leading `dream-` recovers the alias key when the literal
# input doesn't match an alias.
sr_resolve() {
    sr_load
    local input="$1"
    if [[ -z "${SERVICE_ALIASES[$input]:-}" && "$input" == dream-* ]]; then
        local _stripped="${input#dream-}"
        [[ -n "${SERVICE_ALIASES[$_stripped]:-}" ]] && input="$_stripped"
    fi
    echo "${SERVICE_ALIASES[$input]:-$input}"
}

# Get container name for a service ID
sr_container() {
    sr_load
    local sid
    sid=$(sr_resolve "$1")
    echo "${SERVICE_CONTAINERS[$sid]:-dream-$sid}"
}

# Get compose fragment path for a service ID
sr_compose_file() {
    sr_load
    local sid
    sid=$(sr_resolve "$1")
    echo "${SERVICE_COMPOSE[$sid]:-}"
}

# List all service IDs
sr_list_all() {
    sr_load
    printf '%s\n' "${SERVICE_IDS[@]}"
}

# List enabled services (have compose fragments that exist)
sr_list_enabled() {
    sr_load
    for sid in "${SERVICE_IDS[@]}"; do
        local cf="${SERVICE_COMPOSE[$sid]}"
        [[ -n "$cf" && -f "$cf" ]] && echo "$sid"
    done
}

# Get display name for a service ID
sr_service_names() {
    sr_load
    for sid in "${SERVICE_IDS[@]}"; do
        printf '%s\t%s\n' "$sid" "${SERVICE_NAMES[$sid]:-$sid}"
    done
}

# Build compose -f flags for all enabled extension services
sr_compose_flags() {
    sr_load

    # Return cached result if available
    if [[ "$_SR_COMPOSE_FLAGS_CACHED" == "true" ]]; then
        ((_SR_CACHE_HITS++))
        echo "$_SR_COMPOSE_FLAGS_CACHE"
        return 0
    fi

    # Cache miss: rebuild flags
    ((_SR_CACHE_MISSES++))
    local flags=""
    for sid in "${SERVICE_IDS[@]}"; do
        local cf="${SERVICE_COMPOSE[$sid]}"
        [[ -n "$cf" && -f "$cf" ]] && flags="$flags -f $cf"
    done

    # Store in cache
    _SR_COMPOSE_FLAGS_CACHE="$flags"
    _SR_COMPOSE_FLAGS_CACHED=true

    echo "$flags"
}

# Invalidate compose flags cache (call when extensions directory changes)
sr_cache_invalidate() {
    _SR_COMPOSE_FLAGS_CACHED=false
    _SR_COMPOSE_FLAGS_CACHE=""
}

# Get cache statistics for debugging
sr_cache_stats() {
    echo "Cache Hits: $_SR_CACHE_HITS"
    echo "Cache Misses: $_SR_CACHE_MISSES"
    if [[ $_SR_CACHE_MISSES -gt 0 ]]; then
        local total=$((_SR_CACHE_HITS + _SR_CACHE_MISSES))
        local hit_rate=$((_SR_CACHE_HITS * 100 / total))
        echo "Hit Rate: ${hit_rate}%"
    fi
}
