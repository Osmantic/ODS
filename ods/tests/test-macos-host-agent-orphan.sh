#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/macos/lib/host-agent-listener.sh"

listener="4242"
owner_uid="$(id -u)"
process_command="/usr/bin/python3 /Users/test/ods/bin/ods-host-agent.py --install-dir /Users/test/ods"
terminated=""
linger=false

lsof() { [[ -n "$listener" ]] && printf '%s\n' "$listener"; }
ps() {
    if [[ "$*" == *"uid="* ]]; then
        printf '%s\n' "$owner_uid"
    else
        printf '%s\n' "$process_command"
    fi
}
kill() {
    if [[ "$1" == -TERM ]]; then
        terminated="$2"
        return 0
    fi
    [[ "$linger" == true ]]
}
sleep() { :; }

macos_retire_owned_host_agent_listener 127.0.0.1 7710 /Users/test/ods
[[ "$terminated" == 4242 ]]

terminated=""
owner_uid=999999
if macos_retire_owned_host_agent_listener 127.0.0.1 7710 /Users/test/ods; then exit 1; fi
[[ -z "$terminated" ]]

owner_uid="$(id -u)"
process_command="/usr/bin/python3 /Users/other/ods/bin/ods-host-agent.py --install-dir /Users/other/ods"
if macos_retire_owned_host_agent_listener 127.0.0.1 7710 /Users/test/ods; then exit 1; fi
[[ -z "$terminated" ]]

listener=""
macos_retire_owned_host_agent_listener 127.0.0.1 7710 /Users/test/ods
[[ -z "$terminated" ]]

echo "[OK] macOS host-agent listener recovery retires only the exact owned process"
