#!/bin/bash
# ============================================================================
# ODS Installer — Background Task Management
# ============================================================================
# Part of: installers/lib/
# Purpose: Track and verify completion of background processes
#
# Expects: LOG_FILE, ai(), ai_ok(), ai_warn(), ai_bad()
# Provides: bg_task_start(), bg_task_wait(), bg_task_status()
#
# Modder notes:
#   Add new background task types here.
# ============================================================================

# Registry file for background tasks
BG_TASK_REGISTRY="${BG_TASK_REGISTRY:-/tmp/ods-bg-tasks.json}"

# Start tracking a background task
# Usage: bg_task_start <task_id> <pid> <description> <log_file>
bg_task_start() {
    local task_id="$1"
    local pid="$2"
    local description="$3"
    local log_file="$4"
    
    # Create registry if it doesn't exist
    if [[ ! -f "$BG_TASK_REGISTRY" ]]; then
        echo "[]" > "$BG_TASK_REGISTRY"
    fi
    
    # Add task to registry
    python3 - "$BG_TASK_REGISTRY" "$task_id" "$pid" "$description" "$log_file" <<'PY'
import json
import sys
from pathlib import Path

registry_path = Path(sys.argv[1])
task_id = sys.argv[2]
pid = int(sys.argv[3])
description = sys.argv[4]
log_file = sys.argv[5]

tasks = json.loads(registry_path.read_text())
tasks.append({
    "id": task_id,
    "pid": pid,
    "description": description,
    "log_file": log_file,
    "status": "running"
})
registry_path.write_text(json.dumps(tasks, indent=2))
PY
}

# Check status of a background task
# Usage: bg_task_status <task_id>
# Returns: 0 if running, 1 if completed successfully, 2 if failed, 3 if not found
bg_task_status() {
    local task_id="$1"
    
    if [[ ! -f "$BG_TASK_REGISTRY" ]]; then
        return 3
    fi
    
    python3 - "$BG_TASK_REGISTRY" "$task_id" <<'PY'
import json
import sys
import os
from pathlib import Path

registry_path = Path(sys.argv[1])
task_id = sys.argv[2]

if not registry_path.exists():
    sys.exit(3)

tasks = json.loads(registry_path.read_text())
task = next((t for t in tasks if t["id"] == task_id), None)

if not task:
    sys.exit(3)

pid = task["pid"]

# Check if process is still running
try:
    os.kill(pid, 0)
    sys.exit(0)  # Running
except OSError:
    # Process not running - check log for success/failure
    log_file = task.get("log_file", "")
    if log_file and Path(log_file).exists():
        log_content = Path(log_file).read_text()
        if "ERROR" in log_content or "FAILED" in log_content or "failed" in log_content:
            sys.exit(2)  # Failed
        else:
            sys.exit(1)  # Completed
    sys.exit(1)  # Assume completed if no log
PY
}

# Wait for a background task to complete with timeout
# Usage: bg_task_wait <task_id> <timeout_seconds> [check_interval]
# Returns: 0 if completed successfully, 1 if failed, 2 if timeout
bg_task_wait() {
    local task_id="$1"
    local timeout="${2:-1200}"
    local check_interval="${3:-5}"
    local elapsed=0
    
    while [[ $elapsed -lt $timeout ]]; do
        bg_task_status "$task_id"
        local status=$?
        
        case $status in
            0)  # Still running
                sleep "$check_interval"
                elapsed=$((elapsed + check_interval))
                ;;
            1)  # Completed successfully
                return 0
                ;;
            2)  # Failed
                return 1
                ;;
            3)  # Not found
                return 1
                ;;
        esac
    done
    
    # Timeout
    return 2
}

# Get summary of all background tasks
# Usage: bg_task_summary
bg_task_summary() {
    if [[ ! -f "$BG_TASK_REGISTRY" ]]; then
        echo "No background tasks registered"
        return
    fi
    
    python3 - "$BG_TASK_REGISTRY" <<'PY'
import json
import sys
import os
from pathlib import Path

registry_path = Path(sys.argv[1])
tasks = json.loads(registry_path.read_text())

if not tasks:
    print("No background tasks registered")
    sys.exit(0)

print(f"Background tasks: {len(tasks)}")
for task in tasks:
    task_id = task["id"]
    pid = task["pid"]
    desc = task["description"]
    
    # Check if still running
    try:
        os.kill(pid, 0)
        status = "running"
    except OSError:
        log_file = task.get("log_file", "")
        if log_file and Path(log_file).exists():
            log_content = Path(log_file).read_text()
            if "ERROR" in log_content or "failed" in log_content:
                status = "failed"
            else:
                status = "completed"
        else:
            status = "completed"
    
    print(f"  [{task_id}] {desc}: {status}")
PY
}
