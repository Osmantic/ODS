#!/usr/bin/env bash
set -euo pipefail

action="$1"
install_dir="$2"
binary="$3"
pid_file="$4"
shift 4
label=com.ods.llama-server
domain="gui/$(id -u)"
plist="$HOME/Library/LaunchAgents/$label.plist"

stop_loaded_service() {
    local status previous_pid attempt
    if ! status="$(launchctl print "$domain/$label" 2>/dev/null)"; then
        return 0
    fi
    previous_pid="$(printf '%s\n' "$status" | awk '/^\tpid = [0-9]+$/ {print $3; exit}')"
    launchctl bootout "$domain/$label" || return 1
    # bootout can finish before the job/process is gone. Do not overlap two
    # Metal model allocations or discard recovery files while the old PID lives.
    for attempt in {1..60}; do
        if ! launchctl print "$domain/$label" >/dev/null 2>&1 \
            && { [[ -z "$previous_pid" ]] || ! kill -0 "$previous_pid" 2>/dev/null; }; then
            return 0
        fi
        sleep 0.5
    done
    echo 'Native llama shutdown is not confirmed; refusing to replace its service.' >&2
    return 1
}

stop_install_owned_processes() {
    local line pid command attempt still_running=false

    process_is_live() {
        local state
        state="$(ps -p "$1" -o stat= 2>/dev/null | tr -d '[:space:]')"
        [[ -n "$state" && "$state" != Z* ]]
    }

    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        pid="${line%%[[:space:]]*}"
        command="${line#"$pid"}"
        command="${command#"${command%%[![:space:]]*}"}"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        [[ "$command" == "$binary" || "$command" == "$binary "* \
            || "$command" == *" --model $install_dir/data/models/"* ]] || continue

        kill -TERM "$pid" 2>/dev/null || true
        for attempt in {1..20}; do
            if ! process_is_live "$pid"; then
                break
            fi
            sleep 0.25
        done
        if process_is_live "$pid"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
        if process_is_live "$pid"; then
            echo "Native llama process $pid survived shutdown; refusing to continue." >&2
            still_running=true
        fi
    done < <(ps -axo pid=,command=)
    [[ "$still_running" == false ]]
}

if [[ "$action" == stop ]]; then
    stop_loaded_service || exit 1
    stop_install_owned_processes || exit 1
    [[ ! -f "$plist" ]] || rm "$plist"
    [[ ! -f "$pid_file" ]] || rm "$pid_file"
    exit 0
fi
[[ "$action" == start ]] || exit 2
memory_check="$(dirname "$0")/native-memory-budget.py"
model_path=""
previous=""
for argument in "$@"; do
    [[ "$previous" != --model && "$previous" != -m ]] || model_path="$argument"
    previous="$argument"
done
if [[ -n "$model_path" && -f "$memory_check" ]]; then
    "${ODS_PYTHON_CMD:-python3}" "$memory_check" --model "$model_path" || \
        echo 'ODS: native memory estimate failed; review resource settings.' >&2
fi
stop_loaded_service || exit 1
stop_install_owned_processes || exit 1
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS" "$(dirname "$pid_file")"
"${ODS_PYTHON_CMD:-python3}" - "$plist" "$install_dir" "$binary" "$HOME/Library/Logs/ODS/llama-server.log" "$@" <<'PY'
import os
import pathlib
import plistlib
import sys
import tempfile

destination, install, binary, log, *arguments = sys.argv[1:]
payload = {
    "Label": "com.ods.llama-server",
    "ProgramArguments": [binary, *arguments],
    "WorkingDirectory": install,
    "RunAtLoad": True,
    "StandardOutPath": log,
    "StandardErrorPath": log,
}
fd, staged = tempfile.mkstemp(dir=pathlib.Path(destination).parent, suffix=".plist")
try:
    with os.fdopen(fd, "wb") as handle:
        plistlib.dump(payload, handle)
    os.replace(staged, destination)
finally:
    if os.path.exists(staged):
        os.unlink(staged)
PY
loaded=false
for attempt in {1..10}; do
    if launchctl bootstrap "$domain" "$plist"; then
        loaded=true
        break
    fi
    # launchd may still be releasing a just-booted-out service definition.
    sleep 1
done
$loaded || exit 1
launchctl kickstart "$domain/$label"
for attempt in {1..20}; do
    pid="$(launchctl print "$domain/$label" | awk '/^\tpid = [0-9]+$/ {print $3; exit}')"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        printf '%s\n' "$pid" > "$pid_file"
        exit 0
    fi
    sleep 0.5
done
echo 'Native llama LaunchAgent did not start; check ~/Library/Logs/ODS/llama-server.log' >&2
exit 1
