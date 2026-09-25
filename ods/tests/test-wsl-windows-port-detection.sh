#!/bin/bash
# Verifies that Linux installer preflight sees native Windows listeners while
# running under WSL, without requiring a real Windows host in CI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/powershell.exe" <<'STUB'
#!/bin/bash
count_file="${ODS_TEST_PS_COUNT_FILE:?}"
count=0
[[ -f "$count_file" ]] && count="$(cat "$count_file")"
printf '%s\n' "$((count + 1))" > "$count_file"
printf '3000\r\n9000\r\n'
printf 'ODS_WINDOWS_PORT_SCAN_OK\r\n'
STUB
chmod +x "$tmp/powershell.exe"

cat > "$tmp/docker" <<'STUB'
#!/bin/bash
if [[ "${1:-}" == "ps" && "$*" == *"publish=3000"* ]]; then
    printf 'owned-container\n'
elif [[ "${1:-}" == "inspect" && "${*: -1}" == "owned-container" ]]; then
    printf '/previous/ods/install|ods\n'
fi
STUB
chmod +x "$tmp/docker"

export PATH="$tmp:$PATH"
export ODS_TEST_PS_COUNT_FILE="$tmp/calls"
export ODS_WSL_HOST_OVERRIDE=true
export INSTALL_DIR="$tmp/install"
SCRIPT_DIR="$ROOT_DIR"
source "$ROOT_DIR/installers/lib/detection.sh"
warn() { :; }
_port_check_warned=false
source <(sed -n '/^_phase04_current_install_owns_docker_port\s*()\s*{/,/^}/p;/^check_port_conflict\s*()\s*{/,/^}/p' \
    "$ROOT_DIR/installers/phases/04-requirements.sh")

ods_windows_host_port_in_use 9000
if ods_windows_host_port_in_use 9100; then
    printf 'FAIL: unexpected Windows listener on 9100\n' >&2
    exit 1
fi
ods_windows_host_port_in_use 3000
[[ "$(cat "$ODS_TEST_PS_COUNT_FILE")" == "1" ]]

check_port_conflict 9000
[[ "$PORT_CONFLICT" == "true" ]]
[[ "$PORT_CONFLICT_PROC" == "Windows host process" ]]
if check_port_conflict 3000; then
    printf 'FAIL: current installation container was reported as a conflict\n' >&2
    exit 1
fi
[[ "$PORT_CONFLICT" == "false" ]]
if check_port_conflict 9100; then
    printf 'FAIL: unexpected combined listener conflict on 9100\n' >&2
    exit 1
fi
[[ "$PORT_CONFLICT" == "false" ]]
[[ "$(cat "$ODS_TEST_PS_COUNT_FILE")" == "1" ]]

set +e
ods_windows_host_port_in_use invalid
invalid_rc=$?
set -e
[[ "$invalid_rc" == "2" ]]

printf 'PASS: WSL Windows listener detection is cached and exact\n'
