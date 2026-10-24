#!/bin/bash
# ============================================================================
# reconcile-bootstrap-swap.sh — Settle an interrupted bootstrap model upgrade
# ============================================================================
# Purpose: bootstrap-upgrade.sh force-recreates llama-server during its
#          hot-swap. When the host dies between the container create and its
#          start, the container stays "Created" (restart policies never start
#          those), data/bootstrap-status.json stays active forever and the
#          background task registry keeps a dead pid as "running".
#          This script repairs that state once no upgrade process is alive
#          and the active status is stale.
#
#          Only a "created" llama-server is started: that is the interrupted
#          recreate signature (never started; `docker stop` cannot produce
#          it). An "exited" container may have been stopped on purpose
#          (`ods stop`), so it is left for the next `ods start`; the status
#          file and task record are still settled.
#
# Expects: $1 = install dir. docker, python3.
# Provides: exit 0 when nothing to do, a live upgrade owns the state, or the
#           state was reconciled; exit 75 (deferred) when the status is still
#           fresh or the lifecycle lock is busy, so the caller rechecks later.
#           "complete" is written only when the promotion is proven: .env and
#           models.ini both select the full GGUF, llama-server is running with
#           --model /models/<gguf> and /health answers. Anything less is
#           recorded "failed" (interrupted), which `ods start` resumes.
#
# Modder notes:
#   Called by the host agent at boot and by `ods start`. It never acts on a
#   macOS native-llama install (Darwin + GPU_BACKEND=apple): there the model
#   is the launchd job com.ods.llama-server, not the Docker container, and
#   ods-macos.sh owns the bootstrap status. Otherwise it never acts while:
#   - a bootstrap-upgrade.sh process (or the recorded task pid) is alive;
#   - the active status is younger than ODS_BOOTSTRAP_UPGRADE_STALE_SECONDS
#     (default 900). Same knob and default as
#     _ods_cli_bootstrap_upgrade_status_is_stale in ods-cli; keep them in sync.
#     This covers the installer having written the status before its
#     systemd-run/nohup child is visible in ps;
#   - another model lifecycle operation holds the lifecycle lock
#     (installers/lib/model-lifecycle-lock.sh, taken non-blocking here).
# ============================================================================

set -euo pipefail

INSTALL_DIR="${1:?usage: reconcile-bootstrap-swap.sh <install_dir>}"
STATUS_FILE="$INSTALL_DIR/data/bootstrap-status.json"
ENV_FILE="$INSTALL_DIR/.env"
MODELS_INI="$INSTALL_DIR/config/llama-server/models.ini"
EXIT_DEFERRED=75
LLAMA_CONTAINER="${ODS_LLAMA_CONTAINER:-ods-llama-server}"
BG_TASK_REGISTRY="$INSTALL_DIR/logs/background-tasks.json"
BG_TASK_ID="full-model-download"
# Same knob/default as _ods_cli_bootstrap_upgrade_status_is_stale (ods-cli).
STALE_SECONDS="${ODS_BOOTSTRAP_UPGRADE_STALE_SECONDS:-900}"
[[ "$STALE_SECONDS" =~ ^[0-9]+$ ]] || STALE_SECONDS=900
HEALTH_ATTEMPTS="${ODS_BOOTSTRAP_RECONCILE_HEALTH_ATTEMPTS:-12}"
[[ "$HEALTH_ATTEMPTS" =~ ^[0-9]+$ ]] || HEALTH_ATTEMPTS=12
HEALTH_INTERVAL="${ODS_BOOTSTRAP_RECONCILE_HEALTH_INTERVAL:-5}"
[[ "$HEALTH_INTERVAL" =~ ^[0-9]+$ ]] || HEALTH_INTERVAL=5

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

# Take the model lifecycle lock without waiting (the shared acquire helper
# blocks). Returns 1 when another lifecycle operation (bootstrap-upgrade.sh,
# dashboard activation) holds it. The fd stays open until this script exits,
# which releases the lock.
try_model_lifecycle_lock() {
    [[ "$(uname -s)" == "Linux" ]] || return 0
    command -v flock >/dev/null 2>&1 || return 0
    # shellcheck source=../installers/lib/model-lifecycle-lock.sh
    . "$INSTALL_DIR/installers/lib/model-lifecycle-lock.sh"
    local lock_file lock_fd
    lock_file="$(ods_model_lifecycle_lock_file "$INSTALL_DIR")"
    (umask 077 && mkdir -p "$(dirname "$lock_file")")
    exec {lock_fd}>"$lock_file"
    flock -xn "$lock_fd"
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

models_ini_selects() {
    [[ -f "$MODELS_INI" ]] && grep -Eq "^[[:space:]]*filename[[:space:]]*=[[:space:]]*$1[[:space:]]*$" "$MODELS_INI"
}

llama_state() {
    docker ps -a --filter "name=^${LLAMA_CONTAINER}\$" --format '{{.State}}'
}

# The upgrader's own runtime proof: the running container was created with the
# full GGUF as --model, and llama.cpp answers /health only once it is loaded.
runtime_serves() {
    local model="$1" cmd i port
    [[ "$(llama_state)" == "running" ]] || return 1
    cmd="$(docker inspect "$LLAMA_CONTAINER" --format '{{join .Config.Cmd " "}}')"
    [[ "$cmd" == *"/models/${model}"* ]] || return 1
    port="$(env_value OLLAMA_PORT)"
    for (( i = 1; i <= HEALTH_ATTEMPTS; i++ )); do
        curl -sf --max-time 5 "http://127.0.0.1:${port:-8080}/health" >/dev/null && return 0
        (( i < HEALTH_ATTEMPTS )) && sleep "$HEALTH_INTERVAL"
    done
    return 1
}

write_terminal_status() {
    local status="$1" message="$2" model="$3" total="$4"
    local tmp
    tmp="$(umask 077; mktemp "${STATUS_FILE}.tmp.XXXXXX")"
    python3 - "$tmp" "$status" "$model" "$total" "$message" <<'PY'
import json, sys, time
path, status, model, total, message = sys.argv[1:6]
total_bytes = int(total) if total.isdigit() else 0
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "status": status,
        "model": model,
        "percent": 100 if status == "complete" else None,
        "bytesDownloaded": total_bytes if status == "complete" else 0,
        "bytesTotal": total_bytes,
        "speedBytesPerSec": 0,
        "eta": message,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, f, indent=2)
PY
    mv "$tmp" "$STATUS_FILE"
}

[[ -f "$STATUS_FILE" ]] || exit 0
# Same predicate as _ods_cli_macos_native_llama in ods-cli.
if [[ "$(uname -s)" == Darwin && "$(env_value GPU_BACKEND)" == apple ]]; then
    log "macOS native llama-server: bootstrap status is owned by ods-macos.sh; skipping."
    exit 0
fi
status="$(status_field status)"
case "$status" in
    starting|downloading|verifying|swapping) ;;
    *) exit 0 ;;
esac

model="$(status_field model)"
if [[ -n "$model" ]] && upgrade_process_running "$model"; then
    log "Model upgrade for $model is still running; leaving status '$status' alone."
    exit 0
fi

if ! status_is_stale; then
    log "Status '$status' updated within ${STALE_SECONDS}s; the upgrade may still be starting. Deferring."
    exit "$EXIT_DEFERRED"
fi

if ! try_model_lifecycle_lock; then
    log "Another model lifecycle operation holds the lock; deferring status '$status'."
    exit "$EXIT_DEFERRED"
fi

# Container state, scoped to the exact name; empty when it does not exist.
state="$(llama_state)"
if [[ "$state" == "created" ]]; then
    log "Starting $LLAMA_CONTAINER left 'created' by an interrupted model upgrade..."
    docker start "$LLAMA_CONTAINER" >/dev/null
elif [[ -n "$state" && "$state" != "running" ]]; then
    log "$LLAMA_CONTAINER is '$state'; leaving it for 'ods start'."
fi

total="$(status_field bytesTotal)"
# .env is written before models.ini and the runtime swap, so it alone is not
# completion evidence: prove the configuration and the selected runtime.
if [[ -n "$model" && "$(env_value GGUF_FILE)" == "$model" ]] \
        && models_ini_selects "$model" && runtime_serves "$model"; then
    write_terminal_status complete "" "$model" "$total"
    log "Marked interrupted model upgrade complete: llama-server serves $model."
else
    write_terminal_status failed "interrupted: upgrade process stopped during '$status'" "$model" "$total"
    log "Marked interrupted model upgrade failed (was '$status'); 'ods start' retries it."
fi

# shellcheck source=../installers/lib/background-tasks.sh
. "$INSTALL_DIR/installers/lib/background-tasks.sh"
bg_task_mark_interrupted "$BG_TASK_ID"
