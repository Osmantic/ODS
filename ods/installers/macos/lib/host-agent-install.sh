#!/bin/bash
# Shared host-agent setup for macOS installation and retained-install recovery.
# Callers provide logging, environment/bind helpers and bridge-manager.sh.

# launchd does not inherit the login shell PATH. Include Docker and Homebrew.
_compute_launchd_path() {
    local extra="${1:-}"
    local docker_bin="" docker_dir="" brew_prefix=""
    if command -v docker >/dev/null 2>&1; then
        docker_bin="$(command -v docker)"
        docker_dir="$(cd "$(dirname "$docker_bin")" && pwd)"
    fi
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix)"
    fi
    local entries=()
    [[ -n "$extra" ]]                && entries+=("$extra")
    [[ -n "$docker_dir" ]]           && entries+=("$docker_dir")
    [[ -n "$brew_prefix" ]]          && entries+=("${brew_prefix}/bin")
    entries+=("/opt/homebrew/bin" "/usr/local/bin" "/usr/bin" "/bin")
    local seen=":" path_out="" d
    for d in "${entries[@]}"; do
        case "$seen" in
            *":${d}:"*) ;;
            *) seen="${seen}${d}:"; path_out="${path_out:+${path_out}:}${d}" ;;
        esac
    done
    printf '%s' "$path_out"
}


_configure_macos_host_agent_bridge() {
    local env_file="${INSTALL_DIR}/.env"
    local enabled listen_host allowed_peer agent_port agent_bind
    enabled="$(read_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED")"
    listen_host="$(read_env_value "$env_file" "ODS_MACOS_HOST_GATEWAY")"
    allowed_peer="$(read_env_value "$env_file" "ODS_MACOS_VM_IP")"
    agent_port="$(read_env_value "$env_file" "ODS_AGENT_PORT")"
    agent_bind="$(read_env_value "$env_file" "ODS_AGENT_BIND")"
    [[ -n "$agent_bind" ]] || agent_bind="127.0.0.1"
    if [[ "$enabled" == "true" ]] && macos_bind_uses_direct_gateway "$agent_bind" "$listen_host"; then
        ai "Host-agent bind ${agent_bind} already covers the Colima gateway; disabling the host-agent bridge"
        enabled="false"
        upsert_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED" "false"
    fi
    [[ "$agent_port" =~ ^[0-9]+$ ]] || agent_port="7710"
    macos_configure_port_bridge "$enabled" "$HOST_AGENT_BRIDGE_PLIST_LABEL" \
        "$HOST_AGENT_BRIDGE_PLIST" "$HOST_AGENT_BRIDGE_LOG" "Colima host-agent bridge" \
        "$listen_host" "$agent_port" "$agent_port" "$allowed_peer" "$INSTALL_DIR"
}

_ensure_macos_agent_python() {
    local bootstrap_python="$1"
    local venv_dir="${INSTALL_DIR}/.venv/host-agent"
    local runtime="${venv_dir}/bin/python"
    if [[ ! -x "$runtime" ]]; then
        "$bootstrap_python" -m venv "$venv_dir" >>"$ODS_LOG_FILE" 2>&1 || return 1
    fi
    if ! "$runtime" -c 'import yaml, huggingface_hub, hf_xet' >/dev/null 2>&1; then
        "$runtime" -m pip install --quiet pyyaml 'huggingface_hub[hf_xet]>=0.27' \
            >>"$ODS_LOG_FILE" 2>&1 || return 1
    fi
    "$runtime" -c 'import yaml, huggingface_hub, hf_xet' >/dev/null 2>&1 || return 1
    AGENT_PYTHON="$runtime"
}


_verify_macos_dashboard_host_agent() {
    local env_file="$1"
    local container_state bridge_enabled host port api_key attempt

    container_state="$(docker inspect --format '{{.State.Status}}' ods-dashboard-api 2>/dev/null || true)"
    if [[ "$container_state" != "running" ]]; then
        ai_err "Dashboard API container is not running (state: ${container_state:-missing})."
        ai "  Inspect: docker logs ods-dashboard-api"
        return 1
    fi

    bridge_enabled="$(read_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED")"
    host="$(read_env_value "$env_file" "ODS_AGENT_HOST")"
    port="$(read_env_value "$env_file" "ODS_AGENT_PORT")"
    api_key="$(read_env_value "$env_file" "ODS_AGENT_KEY")"
    [[ -n "$host" ]] || host="host.docker.internal"
    [[ "$port" =~ ^[0-9]+$ ]] || port="7710"
    if [[ -z "$api_key" ]]; then
        ai_err "Cannot verify the dashboard host-agent path because ODS_AGENT_KEY is empty."
        return 1
    fi

    for attempt in $(seq 1 20); do
        # The key goes through stdin, never argv, which any local user can read.
        if printf 'Authorization: Bearer %s\n' "$api_key" \
            | docker exec -i ods-dashboard-api curl -fsS --max-time 2 \
            -H @- \
            "http://${host}:${port}/v1/model/status" >/dev/null 2>&1; then
            ai_ok "Dashboard container reached the authenticated host agent"
            return 0
        fi
        sleep 1
    done

    ai_err "Dashboard container cannot reach the authenticated host agent at ${host}:${port}."
    ai "  Host log:   $HOME/Library/Logs/ODS/ods-host-agent.log"
    [[ "$bridge_enabled" == "true" ]] && ai "  Bridge log: $HOST_AGENT_BRIDGE_LOG"
    return 1
}


ods_macos_install_host_agent() {
local _agent_native_bind _agent_probe_host _agent_bootstrap_err _agent_bootstrap_rc
local _agent_health_ok _agent_health_i
AGENT_PYTHON="$(command -v python3 || true)"
if [[ -f "${INSTALL_DIR}/bin/ods-host-agent.py" ]] && [[ -n "$AGENT_PYTHON" ]]; then
    # xpcproxy permits log creation under HOME even for external-volume installs.
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS" || return 1
    ODS_AGENT_PATH="$(_compute_launchd_path "")" || return 1
    _agent_native_bind="$(read_env_value "$INSTALL_DIR/.env" "ODS_AGENT_BIND")"
    _agent_native_bind="$(macos_normalize_agent_bind "${_agent_native_bind:-127.0.0.1}")"
    _agent_probe_host="$(macos_bind_probe_host "$_agent_native_bind")"
    if ! command -v docker >/dev/null 2>&1; then
        ai_warn "docker not found on PATH at install time — host agent will fail to start until Docker Desktop is launched and 'docker' resolves on your shell PATH"
    fi
    ai "Preparing isolated ODS host-agent Python runtime..."
    if ! _ensure_macos_agent_python "$AGENT_PYTHON"; then
        ai_err "Could not prepare host-agent Python dependencies. See $ODS_LOG_FILE."
        return 1
    fi
    ODS_AGENT_PORT="$(read_env_value "$INSTALL_DIR/.env" "ODS_AGENT_PORT")"
    ODS_AGENT_PORT="${ODS_AGENT_PORT:-7710}"
    if ! cat > "$ODS_AGENT_PLIST" <<AGENT_PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${ODS_AGENT_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${AGENT_PYTHON}</string>
        <string>${INSTALL_DIR}/bin/ods-host-agent.py</string>
        <string>--install-dir</string>
        <string>${INSTALL_DIR}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ODS_HOME</key>
        <string>${INSTALL_DIR}</string>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${ODS_AGENT_PATH}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/ODS/ods-host-agent.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/ODS/ods-host-agent.log</string>
</dict>
</plist>
AGENT_PLIST_EOF
    then
        ai_err "Could not write the ODS host-agent LaunchAgent."
        return 1
    fi

    # Older retained agents do not export their own Python for resolver children.
    # Pin the venv that just proved its dependencies, including outside recovery.
    # Recovery also pins a verified socket; launchd inherits neither setting.
    if ! "$AGENT_PYTHON" - "$ODS_AGENT_PLIST" "$AGENT_PYTHON" <<'AGENT_DOCKER_ENV_PY'
import os
from pathlib import Path
import plistlib
import sys
import tempfile

path = Path(sys.argv[1])
document = plistlib.loads(path.read_bytes())
environment = document["EnvironmentVariables"]
environment["ODS_PYTHON_CMD"] = sys.argv[2]
if os.environ.get("DOCKER_HOST"):
    for key in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment[key] = os.environ.get(key, "")
with tempfile.TemporaryDirectory(prefix=".ods-host-agent-", dir=path.parent) as temporary:
    staged = Path(temporary) / "agent.plist"
    with staged.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(document, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staged, path)
AGENT_DOCKER_ENV_PY
    then
        ai_err "Could not preserve the host-agent Python and Docker environment."
        return 1
    fi

    launchctl bootout "gui/$(id -u)/${ODS_AGENT_PLIST_LABEL}" >/dev/null 2>&1 || true
    if ! macos_retire_owned_host_agent_listener "$_agent_probe_host" "$ODS_AGENT_PORT" "$INSTALL_DIR"; then
        ai_err "Port ${ODS_AGENT_PORT} still has a listener that cannot be safely retired as this ODS host agent."
        return 1
    fi
    _agent_bootstrap_err="$(launchctl bootstrap "gui/$(id -u)" "$ODS_AGENT_PLIST" 2>&1)" && _agent_bootstrap_rc=0 || _agent_bootstrap_rc=$?
    if [[ $_agent_bootstrap_rc -eq 0 ]]; then
        # `launchctl bootstrap` can succeed (definition loaded) while launchd
        # leaves the service in "pended nondemand spawn = speculative" and
        # never actually launches the process — common right after a
        # same-session bootout because the throttler hasn't reset yet, and
        # `RunAtLoad=true` doesn't override the throttle. Force the spawn
        # with `kickstart`, then poll /health so we don't report success
        # while the agent is still down. Without this verification the
        # dashboard-api will hit "Host agent unreachable" on every model and
        # extension action even though the installer printed [OK].
        launchctl kickstart -p "gui/$(id -u)/${ODS_AGENT_PLIST_LABEL}" >/dev/null 2>&1 || true
        _agent_health_ok=false
        for _agent_health_i in 1 2 3 4 5 6 7 8 9 10; do
            if curl -fsS --max-time 1 "http://${_agent_probe_host}:${ODS_AGENT_PORT}/health" >/dev/null 2>&1; then
                _agent_health_ok=true
                break
            fi
            sleep 1
        done
        if [[ "$_agent_health_ok" == "true" ]]; then
            ai_ok "ODS host agent installed (LaunchAgent, port ${ODS_AGENT_PORT})"
        else
            ai_warn "ODS host agent loaded but not responding on :${ODS_AGENT_PORT} after 10s."
            ai_warn "  Log:         tail -F ~/Library/Logs/ODS/ods-host-agent.log"
            ai_warn "  Force start: launchctl kickstart -p gui/\$(id -u)/${ODS_AGENT_PLIST_LABEL}"
            ai_warn "  Dashboard model + extension actions will fail until the agent comes up."
            # Keep the authenticated probe's additional startup grace below.
        fi
    else
        ai_warn "ODS host agent LaunchAgent failed (rc=${_agent_bootstrap_rc}): ${_agent_bootstrap_err}"
        if [[ "${_agent_bootstrap_err}" == *"Input/output error"* ]]; then
            ai_warn "launchd may still be releasing the previous service. Wait and retry this setup step; do not reinstall or remove Pixel receipts."
        else
            ai_warn "The host-agent login service was not installed. Keep the installation intact and inspect the launchd error above."
        fi
        return 1
    fi
else
    [[ ! -f "${INSTALL_DIR}/bin/ods-host-agent.py" ]] && ai_warn "Host agent script not found, skipping"
    [[ -z "$AGENT_PYTHON" ]] && ai_warn "python3 not found, host agent not installed"
    return 1
fi
if ! _configure_macos_host_agent_bridge; then
    return 1
fi
if ! _verify_macos_dashboard_host_agent "$INSTALL_DIR/.env"; then
    return 1
fi
}
