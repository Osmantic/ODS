#!/usr/bin/env bash

# launchctl bootout can leave an old host-agent process listening after the
# LaunchAgent definition is gone. Retire only a listener owned by this user
# whose complete command identifies this exact ODS installation.
macos_retire_owned_host_agent_listener() {
    local probe_host="$1" port="$2" install_dir="$3"
    local listener_pids pid observed_uid observed_command attempt

    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    command -v lsof >/dev/null 2>&1 || return 1
    listener_pids="$(lsof -nP -t -iTCP@"${probe_host}":"${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
    [[ -n "$listener_pids" ]] || return 0

    while IFS= read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || return 1
        observed_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d '[:space:]')"
        observed_command="$(ps -p "$pid" -o command= 2>/dev/null)"
        [[ "$observed_uid" == "$(id -u)" ]] || return 1
        [[ "$observed_command" == *"${install_dir}/bin/ods-host-agent.py --install-dir ${install_dir}" ]] || return 1

        kill -TERM "$pid" || return 1
        for (( attempt = 0; attempt < 10; attempt++ )); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && return 1
    done <<< "$listener_pids"
    return 0
}
