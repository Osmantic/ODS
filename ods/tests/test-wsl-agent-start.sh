#!/usr/bin/env bash
# Exercise the shipped start command and address helper with inert OS boundaries.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d "$ROOT/tests/.wsl-agent-start.XXXXXXXX")"
[[ "$fixture" == "$ROOT"/tests/.wsl-agent-start.* && -d "$fixture" ]]
trap 'rm -rf -- "$fixture"' EXIT
INSTALL_DIR="$fixture"
events="$fixture/events"
mkdir -p "$fixture/lib"
cp "$ROOT/lib/wsl-agent-address.sh" "$fixture/lib/wsl-agent-address.sh"
: > "$fixture/lib/wsl-agent-address.py"
start_source="$(sed -n '/^cmd_start() {$/,/^}$/p' "$ROOT/ods-cli")"
[[ -n "$start_source" ]]
eval "$start_source"

# A changed NAT address, no sudo credentials, and no real system commands.
uname() { printf 'Linux\n'; }
python3() {
    if [[ "$1" == "$INSTALL_DIR/lib/wsl-agent-address.py" ]]; then
        printf 'prepare\n' >> "$events"
        [[ "${prepare_failure:-false}" == false ]] || return 72
        printf 'ODS_AGENT_HOST=10.2.3.4\n' > "$INSTALL_DIR/.env"
        printf '{"changed":%s,"mode":"wsl-nat","address":"10.2.3.4"}\n' "$fixture_changed"
    elif [[ "$1" == -c ]]; then
        cat >/dev/null
        printf '%s\n' "$fixture_changed"
    else
        # Ownership code is covered by Python contracts; never execute it here.
        cat >/dev/null
    fi
}
systemctl() {
    [[ "$1" == cat ]] && return 0
    printf 'privileged-restart\n' >> "$events"
    return 73
}
sudo() { printf 'privileged-restart\n' >> "$events"; return 73; }
check_install() { :; }
load_env() { printf 'load-env\n' >> "$events"; }
ensure_llama_cpu_budget() { :; }
sr_load() { :; }
get_compose_flags() { printf '%s\n' '-f fixture.yml'; }
_ods_cli_proxy_enabled() { return 1; }
_ods_cli_refresh_soul() { :; }
_ods_cli_wait_for_bootstrap_compose_safe() { :; }
_ods_cli_reload_model_env() { :; }
_ods_cli_repair_rootless_ownership() { :; }
_ods_cli_maybe_resume_bootstrap_upgrade() { :; }
_compose_run_with_summary() { printf 'compose\n' >> "$events"; }
error() { printf '%s\n' "$*" >&2; exit 1; }

fixture_changed=true
: > "$events"
rc=0
(cmd_start) > "$fixture/output" 2>&1 || rc=$?
[[ "$rc" != 0 ]]
grep -qx privileged-restart "$events"
if grep -qx compose "$events"; then exit 1; fi
printf 'PASS: direct CLI retains its existing privilege failure before Compose\n'

for fixture_changed in true false; do
    : > "$events"
    (cmd_start --defer-wsl-agent-restart)
    [[ "$(cat "$events")" == $'load-env\nprepare\nload-env\ncompose' ]]
    grep -qx 'ODS_AGENT_HOST=10.2.3.4' "$fixture/.env"
    printf 'PASS: Windows-owned start refreshes/reloads NAT and starts Compose without sudo (changed=%s)\n' "$fixture_changed"
done

: > "$events"
prepare_failure=true
rc=0
(cmd_start --defer-wsl-agent-restart) > "$fixture/output" 2>&1 || rc=$?
[[ "$rc" != 0 ]]
if grep -qx compose "$events"; then exit 1; fi
if grep -qx privileged-restart "$events"; then exit 1; fi
printf 'PASS: deferral never bypasses an address-preparation failure\n'

prepare_failure=false
: > "$events"
rc=0
(cmd_start --defer-wsl-agent-restart dashboard) > "$fixture/output" 2>&1 || rc=$?
[[ "$rc" != 0 ]]
if grep -qx prepare "$events"; then exit 1; fi
if grep -qx compose "$events"; then exit 1; fi
printf 'PASS: restart deferral is restricted to the complete lifecycle start\n'
