#!/bin/bash
# ============================================================================
# reconcile-bootstrap-swap.sh — Settle an interrupted bootstrap model upgrade
# ============================================================================
# Purpose: bootstrap-upgrade.sh force-recreates llama-server during its
#          hot-swap. When the host dies between the container create and its
#          start, the container stays "Created" (restart policies never start
#          those), data/bootstrap-status.json stays active forever and the
#          background task registry keeps a dead pid as "running".
#          This script repairs that state once no upgrade process is alive.
#
# Expects: $1 = install dir. docker, python3.
# Provides: exit 0 when nothing to do or the state was reconciled.
#
# Modder notes:
#   Called by the host agent at boot and by `ods start`. It never acts while a
#   bootstrap-upgrade.sh process (or the recorded task pid) is alive.
# ============================================================================

set -euo pipefail

INSTALL_DIR="${1:?usage: reconcile-bootstrap-swap.sh <install_dir>}"
STATUS_FILE="$INSTALL_DIR/data/bootstrap-status.json"
ENV_FILE="$INSTALL_DIR/.env"
LLAMA_CONTAINER="${ODS_LLAMA_CONTAINER:-ods-llama-server}"
BG_TASK_REGISTRY="$INSTALL_DIR/logs/background-tasks.json"
BG_TASK_ID="full-model-download"

log() { printf '[bootstrap-reconcile] %s\n' "$*"; }

status_field() {
    python3 - "$STATUS_FILE" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2])
print("" if value is None else value)
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

env_gguf_file() {
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n 's/^GGUF_FILE=//p' "$ENV_FILE" | tail -n 1 | tr -d "\"'"
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

# Container state, scoped to the exact name; empty when it does not exist.
llama_state="$(docker ps -a --filter "name=^${LLAMA_CONTAINER}\$" --format '{{.State}}')"
if [[ "$llama_state" == "created" || "$llama_state" == "exited" ]]; then
    log "Starting $LLAMA_CONTAINER left '$llama_state' by an interrupted model upgrade..."
    docker start "$LLAMA_CONTAINER" >/dev/null
fi

total="$(status_field bytesTotal)"
if [[ -n "$model" && "$(env_gguf_file)" == "$model" ]]; then
    write_terminal_status complete "" "$model" "$total"
    log "Marked interrupted model upgrade complete: .env already serves $model."
else
    write_terminal_status failed "interrupted: upgrade process stopped during '$status'" "$model" "$total"
    log "Marked interrupted model upgrade failed (was '$status'); 'ods start' retries it."
fi

# shellcheck source=../installers/lib/background-tasks.sh
. "$INSTALL_DIR/installers/lib/background-tasks.sh"
bg_task_mark_interrupted "$BG_TASK_ID"
