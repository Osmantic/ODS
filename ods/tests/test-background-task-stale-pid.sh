#!/bin/bash
# Regression: bg_task_status must not report a recycled pid as "running".
#
# os.kill(pid, 0) only proves SOME process owns the number. If a task's pid is
# reaped and then recycled by an unrelated process, the stale-probe still
# returns running, so bg_task_wait spins to its timeout (~20 min) instead of
# completing. The fix records the process start time at registration and only
# treats a live pid as the task when its start time still matches.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/../installers/lib/background-tasks.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}\u2713${NC} $1"; }
fail() { echo -e "${RED}\u2717${NC} $1"; exit 1; }
info() { echo -e "${BLUE}\u2139${NC} $1"; }

[[ -f "$LIB" ]] || fail "background-tasks.sh not found"

# This check depends on /proc/<pid>/stat (Linux/procfs). On hosts without it
# the start_time is null and the pid-reuse guard is skipped, so skip there.
if [[ ! -e /proc/self/stat ]]; then
    info "procfs not available; skipping stale-pid regression (CI is Linux)"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export BG_TASK_REGISTRY="$TMP/registry.json"

# Minimal stubs required by the lib at source time.
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_bad() { :; }

# shellcheck source=background-tasks.sh
. "$LIB"

# A long-lived task: its pid must be reported as running.
sleep 60 &
LIVE_PID=$!
bg_task_start "task-live" "$LIVE_PID" "live" "$TMP/live.log" >/dev/null

set +e
bg_task_status "task-live"; live_rc=$?
set -e
[[ $live_rc -eq 0 ]] || fail "live task not reported running (rc=$live_rc)"

# Simulate the recorded task being gone and its pid reused by another process:
# steal the live task's pid and record a start_time that no longer matches the
# current one, then a live pid must NOT be reported as the task.
python3 - "$BG_TASK_REGISTRY" <<'PY'
import json
import sys

path = sys.argv[1]
tasks = json.load(open(path))
for t in tasks:
    if t["id"] == "task-live":
        t["start_time"] = "00000000000000000000000000000000000000000"
json.dump(tasks, open(path, "w"))
PY

set +e
bg_task_status "task-live"; stale_rc=$?
set -e
if [[ $stale_rc -eq 0 ]]; then
    fail "recycled pid still reported running (rc=0)"
fi
pass "recycled pid is detected as not running (rc=$stale_rc)"