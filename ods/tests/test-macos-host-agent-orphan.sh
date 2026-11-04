#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/macos/lib/host-agent-listener.sh"

listener="4242"
endpoint="127.0.0.1:7710"
owner_uid="$(id -u)"
process_command="/usr/bin/python3 /Users/test/ods/bin/ods-host-agent.py --install-dir /Users/test/ods"
terminated=""
linger=false

lsof() { [[ -n "$listener" ]] && printf 'p%s\nn%s\n' "$listener" "$endpoint"; }
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

# An occupied port needs a positive identity. A wildcard also conflicts with
# loopback. An unrelated interface, an invisible holder, and a legacy orphan
# cannot identify the owner of this port.
occupied() { cat >/dev/null; printf '127.0.0.1\n'; return 3; }
AGENT_PYTHON=occupied
fixture_loaded_pid=4242
launchctl() { printf 'pid = %s\n' "$fixture_loaded_pid"; }
check_port() { macos_check_host_agent_port 127.0.0.1 7710 /Users/test/ods com.ods.host-agent; }
if check_port; then exit 1; fi
listener=4242
process_command="/usr/bin/python3 /Users/test/ods/bin/ods-host-agent.py"
endpoint="*:7710"
check_port
fixture_loaded_pid=3131
if check_port; then exit 1; fi
fixture_loaded_pid=4242
endpoint="192.0.2.8:7710"
if check_port; then exit 1; fi
[[ -z "$terminated" ]]

# Darwin may allow a more specific bind alongside a wildcard. The existing
# foreign listener still prevents a safe switch of the login service.
free_port() { cat >/dev/null; printf '127.0.0.1\n'; }
AGENT_PYTHON=free_port
endpoint="*:7710"
process_command="/usr/bin/unrelated"
if check_port; then exit 1; fi
endpoint="192.0.2.8:7710"
check_port >/dev/null

echo "[OK] macOS host-agent listener recovery retires only the exact owned process"
