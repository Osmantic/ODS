#!/bin/bash
# ============================================================================
# reconcile-bootstrap-swap.sh — Settle an interrupted bootstrap model upgrade
# ============================================================================
# Purpose: bootstrap-upgrade.sh force-recreates llama-server during its
#          hot-swap. When the host dies between the container create and its
#          start, the container stays "Created" (restart policies never start
#          those), data/bootstrap-status.json stays active forever, the
#          background task registry keeps a dead pid as "running", and
#          `ods start` waits 30 minutes for the dead upgrade and then refuses.
#          Once no upgrade process is alive and the active status is stale,
#          this script:
#          - starts a llama-server left "created" (the interrupted recreate
#            signature; `docker stop` cannot produce it). An "exited" one may
#            have been stopped on purpose (`ods stop`), so it is left for the
#            next `ods start`;
#          - records the run as failed with an "interrupted" message. It never
#            writes "complete": the upgrade is only done after
#            bootstrap-upgrade.sh has reconciled Pixel, patched Hermes,
#            updated Perplexica, removed the bootstrap model and notified the
#            host agent. `ods start` and `ods restart` resume a failed run
#            (_ods_cli_maybe_resume_bootstrap_upgrade in ods-cli), and that run
#            finishes all of it;
#          - marks the stale background task record "interrupted".
#
# Expects: $1 = install dir. docker, python3, and flock on Linux.
# Provides: exit 0 when there is nothing to do, a live upgrade owns the state,
#           or the run was handed to the resume; exit 75 (deferred) when the
#           status is still fresh, Docker is not answering yet, or another
#           model lifecycle operation holds the lock, so the caller rechecks
#           later; exit 1 when the lifecycle lock cannot be taken safely.
#
# Modder notes:
#   Called by the host agent at boot and by `ods start` and `ods restart`. It
#   never acts on a macOS native-llama install (Darwin + GPU_BACKEND=apple):
#   there the model is the launchd job com.ods.llama-server, not the Docker
#   container, and ods-macos.sh owns the bootstrap status. Otherwise it never
#   acts while:
#   - a bootstrap-upgrade.sh process (or the recorded task pid) is alive;
#   - the active status is younger than ODS_BOOTSTRAP_UPGRADE_STALE_SECONDS
#     (default 900). Same knob and default as
#     _ods_cli_bootstrap_upgrade_status_is_stale in ods-cli; keep them in sync.
#     This covers the installer having written the status before its
#     systemd-run/nohup child is visible in ps;
#   - another model lifecycle operation holds the lifecycle lock (the shared
#     installers/lib/model-lifecycle-lock.sh helper, taken without waiting).
#     The status is checked again once the lock is held.
# ============================================================================

set -euo pipefail

INSTALL_DIR="${1:?usage: reconcile-bootstrap-swap.sh <install_dir>}"
STATUS_FILE="$INSTALL_DIR/data/bootstrap-status.json"
ENV_FILE="$INSTALL_DIR/.env"
EXIT_DEFERRED=75
LLAMA_CONTAINER="${ODS_LLAMA_CONTAINER:-ods-llama-server}"
BG_TASK_REGISTRY="$INSTALL_DIR/logs/background-tasks.json"
BG_TASK_ID="full-model-download"
# Same knob/default as _ods_cli_bootstrap_upgrade_status_is_stale (ods-cli).
STALE_SECONDS="${ODS_BOOTSTRAP_UPGRADE_STALE_SECONDS:-900}"
[[ "$STALE_SECONDS" =~ ^[0-9]+$ ]] || STALE_SECONDS=900
status=""
model=""

log() { printf '[bootstrap-reconcile] %s\n' "$*"; }

status_field() {
    python3 - "$STATUS_FILE" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2])
print("" if value is None else value)
PY
}

# Exit 0 when updatedAt is older than STALE_SECONDS. A missing or unparsable
# updatedAt is not stale, matching the ods-cli helper.
status_is_stale() {
    python3 - "$STATUS_FILE" "$STALE_SECONDS" <<'PY'
import calendar, json, sys, time
updated = json.load(open(sys.argv[1], encoding="utf-8")).get("updatedAt")
try:
    epoch = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
except (TypeError, ValueError):
    sys.exit(1)
sys.exit(0 if time.time() - epoch > int(sys.argv[2]) else 1)
PY
}

upgrade_process_running() {
    local model="$1" args
    while IFS= read -r args; do
        case "$args" in
            *"bootstrap-upgrade.sh"*" $model "*) return 0 ;;
        esac
    done < <(ps -eo args=)
    [[ -f "$BG_TASK_REGISTRY" ]] || return 1
    python3 - "$BG_TASK_REGISTRY" "$BG_TASK_ID" <<'PY'
import json, os, sys
tasks = json.load(open(sys.argv[1], encoding="utf-8"))
task = next((t for t in tasks if t.get("id") == sys.argv[2]), None)
pid = task.get("pid") if task else None
if task is None or task.get("status") != "running" or not isinstance(pid, int) or pid <= 0:
    sys.exit(1)
try:
    os.kill(pid, 0)
except ProcessLookupError:
    sys.exit(1)
except PermissionError:
    sys.exit(0)  # pid exists under another user: treat as alive
sys.exit(0)
PY
}

env_value() {
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"'
"
}

llama_state() {
    docker ps -a --filter "name=^${LLAMA_CONTAINER}\$" --format '{{.State}}'
}

# Return 0 when the status is active, stale and owned by no live upgrade;
# 1 when there is nothing to do; 2 when it is too fresh to judge. Sets the
# status and model globals.
check_status() {
    [[ -f "$STATUS_FILE" ]] || return 1
    status="$(status_field status)"
    case "$status" in
        starting|downloading|verifying|swapping) ;;
        *) return 1 ;;
    esac
    model="$(status_field model)"
    if [[ -n "$model" ]] && upgrade_process_running "$model"; then
        log "Model upgrade for $model is still running; leaving status '$status' alone."
        return 1
    fi
    if ! status_is_stale; then
        log "Status '$status' updated within ${STALE_SECONDS}s; the upgrade may still be starting. Deferring."
        return 2
    fi
}

exit_unless_actionable() {
    local rc=0
    check_status || rc=$?
    case "$rc" in
        0) return 0 ;;
        2) exit "$EXIT_DEFERRED" ;;
        *) exit 0 ;;
    esac
}

# Rewrite the status as bootstrap-upgrade.sh's write_status does (same
# directory, mode 0644, atomic replace), keeping its other fields.
mark_interrupted() {
    local message="interrupted: the model upgrade process stopped during '$1' (for example, the computer restarted). Run 'ods start' to finish it. Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC"
    python3 - "$STATUS_FILE" "$message" <<'PY'
import json, os, sys, tempfile, time
path, message = sys.argv[1:3]
with open(path, encoding="utf-8") as stream:
    data = json.load(stream)
data.update(status="failed", speedBytesPerSec=0, eta=message,
            updatedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(data, stream, indent=2)
    stream.write("\n")
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}

[[ -f "$STATUS_FILE" ]] || exit 0
# Same predicate as _ods_cli_macos_native_llama in ods-cli.
if [[ "$(uname -s)" == Darwin && "$(env_value GPU_BACKEND)" == apple ]]; then
    log "macOS native llama-server: bootstrap status is owned by ods-macos.sh; skipping."
    exit 0
fi
exit_unless_actionable

# At boot Docker may still be starting. That is not a failure: defer so the
# host agent checks again. The probe's own output adds nothing to this line.
if ! docker info >/dev/null 2>&1; then
    log "Docker is not answering yet; deferring status '$status'."
    exit "$EXIT_DEFERRED"
fi

# shellcheck source=../installers/lib/model-lifecycle-lock.sh
. "$INSTALL_DIR/installers/lib/model-lifecycle-lock.sh"
lock_rc=0
ods_model_lifecycle_lock_acquire "$INSTALL_DIR" "interrupted model upgrade reconciliation" nowait \
    || lock_rc=$?
case "$lock_rc" in
    0) ;;
    2)
        log "Another model lifecycle operation holds the lock; deferring status '$status'."
        exit "$EXIT_DEFERRED"
        ;;
    *)
        printf '[bootstrap-reconcile] %s\n' \
            "Could not take the model lifecycle lock safely (see above); status '$status' was left for 'ods start'." >&2
        exit 1
        ;;
esac
# A resumed upgrade may have started while this script checked Docker and
# took the lock. Decide again while holding it.
exit_unless_actionable

if ! state="$(llama_state)"; then
    log "Docker stopped answering; deferring status '$status'."
    exit "$EXIT_DEFERRED"
fi
if [[ "$state" == "created" ]]; then
    log "Starting $LLAMA_CONTAINER, left 'created' by the interrupted model upgrade..."
    docker start "$LLAMA_CONTAINER" >/dev/null \
        || log "Could not start $LLAMA_CONTAINER; 'ods start' recreates it."
elif [[ -n "$state" && "$state" != "running" ]]; then
    log "$LLAMA_CONTAINER is '$state'; leaving it for 'ods start'."
fi

mark_interrupted "$status"
log "Marked the interrupted model upgrade failed (was '$status'); 'ods start' or 'ods restart' finishes it."

# shellcheck source=../installers/lib/background-tasks.sh
. "$INSTALL_DIR/installers/lib/background-tasks.sh"
bg_task_mark_interrupted "$BG_TASK_ID"
