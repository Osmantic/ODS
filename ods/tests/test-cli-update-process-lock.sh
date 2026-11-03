#!/usr/bin/env bash
# Real update functions and OS contention; no service, Docker or network calls.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo '[SKIP] Linux flock protocol'; exit 0; }
fixture=$(mktemp -d)
child=""
cleanup() {
    if [[ -n "$child" ]]; then kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; fi
    rm -rf "$fixture"
}
trap cleanup EXIT
export ODS_MODEL_LIFECYCLE_LOCK_ROOT="$fixture/locks"
unset ODS_MODEL_LIFECYCLE_LOCK_FD ODS_MODEL_LIFECYCLE_LOCK_FILE
SCRIPT_DIR="$ROOT"
INSTALL_DIR="$fixture/install"
mkdir "$INSTALL_DIR"
printf '#!/bin/sh\nexit 0\n' > "$INSTALL_DIR/ods-update.sh"
chmod +x "$INSTALL_DIR/ods-update.sh"
extract() {
    awk -v name="$1" '$0 ~ "^" name "\\(\\) [({]" {copy=1} copy{print} copy && /^[})]$/{exit}' "$ROOT/ods-cli"
}
check_install() { :; }
load_env() { :; }
log() { :; }
warn() { :; }
success() { :; }
log_error() { printf '%s\n' "$*" >&2; }
error() { log_error "$*"; exit 1; }
_check_version_compat() { return 0; }
_agent_model_lifecycle_busy() { [[ ! -f "$fixture/recovery" ]] || printf 'recovery pending\n'; }
_env_get_raw() { printf 'fixture-key\n'; }
_env_set() { :; }
ensure_llama_cpu_budget() { :; }
get_compose_flags() { printf '%s\n' '-f fixture.yml'; }
_ods_cli_wait_for_bootstrap_compose_safe() { :; }
_ods_cli_network_access_enabled() { return 1; }
_compose_run_with_summary() { touch "$fixture/recreated"; }
docker() { printf 'dashboard\n'; }
sleep() { :; }
cmd_agent() { :; }
cmd_status() { :; }
_ods_cli_pull_external_images() {
    # A separate process attempts to begin a model operation after the CLI's
    # initial point-in-time gate, while the real update owns the process lease.
    python3 - "$ROOT/bin" "$INSTALL_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import model_lifecycle_lock as locks
try:
    lease = locks.acquire(sys.argv[2])
except BlockingIOError:
    pass
else:
    lease.close()
    raise AssertionError("model operation overlapped update")
PY
}
for fn in cmd_update _ods_cli_run_update_locked _ods_cli_do_update _ods_cli_refuse_under_model_lifecycle; do
    eval "$(extract "$fn")"
done
cmd_update
[[ -f "$fixture/recreated" ]]
echo '[PASS] real CLI update excludes a separate model process after initial admission'

# The recovery status must be rechecked after the initial gate and bootstrap wait.
rm "$fixture/recreated"
_ods_cli_wait_for_bootstrap_compose_safe() { touch "$fixture/recovery"; }
if (cmd_update) >"$fixture/refused.log" 2>&1; then
    echo '[FAIL] update ignored recovery that appeared after its first check' >&2
    exit 1
fi
[[ ! -f "$fixture/recreated" ]]
grep -q 'recovery pending' "$fixture/refused.log"
echo '[PASS] recovery appearing after initial admission prevents mutation under the lease'

# The actual CLI daemon detach helper must close only the child's lease copy.
. "$ROOT/installers/lib/model-lifecycle-lock.sh"
ods_model_lifecycle_lock_acquire "$INSTALL_DIR" test-parent
eval "$(extract _ods_cli_detach_lifecycle_lock)"
(
    _ods_cli_detach_lifecycle_lock
    [[ -z "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" && -z "${ODS_MODEL_LIFECYCLE_LOCK_FILE:-}" ]]
    touch "$fixture/detached"
    exec /bin/sleep 15
) &
child=$!
for ((i=0;i<100;i++)); do [[ ! -e "$fixture/detached" ]] || break; /bin/sleep .02; done
[[ -e "$fixture/detached" ]]
ods_model_lifecycle_lock_release
python3 - "$ROOT/bin" "$INSTALL_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import model_lifecycle_lock as locks
lease = locks.acquire(sys.argv[2])
lease.close()
PY
kill -0 "$child"
echo '[PASS] detached CLI daemon remains alive without retaining the parent lease'
