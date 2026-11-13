#!/usr/bin/env bash

# launchctl bootout can leave an old host-agent process listening after the
# LaunchAgent definition is gone. Retire only a listener owned by this user
# whose complete command identifies this exact ODS installation.
_macos_host_agent_pid_owned() {
    local pid="$1" install_dir="$2" loaded_pid="${3:-}" observed_uid observed_command
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    observed_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d '[:space:]')"
    observed_command="$(ps -p "$pid" -o command= 2>/dev/null)" || return 1
    [[ "$observed_uid" == "$(id -u)" ]] || return 1
    [[ "$observed_command" == *" $install_dir/bin/ods-host-agent.py --install-dir $install_dir" ]] && return 0
    # Older login jobs omitted --install-dir. Only preflight accepts the exact
    # registered job in that form. An ambiguous orphan is never signalled.
    [[ "$pid" == "$loaded_pid" && "$observed_command" == *" $install_dir/bin/ods-host-agent.py" ]]
}

# A bound port may have several listeners. Check every visible conflict before
# touching the working service. lsof's address-specific filter misses wildcard
# listeners, so match its numeric endpoint fields explicitly.
macos_owned_host_agent_listener_pids() {
    local bind="$1" port="$2" install_dir="$3" loaded_pid="${4:-}" listener_pids pid
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    command -v lsof >/dev/null 2>&1 || return 1
    listener_pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fpn 2>/dev/null | awk -v bind="$bind" -v port="$port" '
        /^p/ { pid = substr($0, 2) }
        /^n/ {
            endpoint = substr($0, 2)
            if (bind == "0.0.0.0" || bind == "::" || endpoint == "*:" port ||
                endpoint == bind ":" port || endpoint == "[" bind "]:" port) print pid
        }' | sort -u || true)"
    [[ -n "$listener_pids" ]] || return 0
    while IFS= read -r pid; do
        _macos_host_agent_pid_owned "$pid" "$install_dir" "$loaded_pid" || return 1
    done <<< "$listener_pids"
    printf '%s\n' "$listener_pids"
}

_macos_probe_host_agent_bind() {
    "$AGENT_PYTHON" -I - "$1" "$2" <<'PORT_PY'
import errno
import socket
import sys

try:
    port = int(sys.argv[2])
    if not 1 <= port <= 65535:
        sys.exit(1)
    # The stock host agent uses HTTPServer's AF_INET socket on Darwin.
    address = socket.gethostbyname(sys.argv[1])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((address, port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            print(address)
            sys.exit(3)
    print(address)
except (ValueError, OSError):
    sys.exit(1)
PORT_PY
}

macos_check_host_agent_port() {
    local bind="$1" port="$2" install_dir="$3" label="$4" resolved loaded_pid pids status
    command -v lsof >/dev/null 2>&1 || return 1
    # Darwin can allow a specific-address bind alongside a wildcard listener.
    # Inspect visible listeners even when the bind succeeds.
    status=0
    resolved="$(_macos_probe_host_agent_bind "$bind" "$port")" || status=$?
    [[ "$status" -eq 0 || "$status" -eq 3 ]] || return 1
    loaded_pid="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk '$1 == "pid" && $2 == "=" { print $3; exit }' || true)"
    pids="$(macos_owned_host_agent_listener_pids "$resolved" "$port" "$install_dir" "$loaded_pid")" || return 1
    [[ "$status" -eq 0 || -n "$pids" ]] || return 1
    printf '%s\n' "$resolved"
}

macos_retire_owned_host_agent_listener() {
    local probe_host="$1" port="$2" install_dir="$3" listener_pids pid attempt
    listener_pids="$(macos_owned_host_agent_listener_pids "$probe_host" "$port" "$install_dir")" || return 1
    [[ -n "$listener_pids" ]] || return 0
    while IFS= read -r pid; do
        _macos_host_agent_pid_owned "$pid" "$install_dir" || return 1
        kill -TERM "$pid" || return 1
        for (( attempt = 0; attempt < 10; attempt++ )); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && return 1
    done <<< "$listener_pids"
    return 0
}
