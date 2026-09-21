#!/bin/sh
set -u

# The controller verifies this regular owner-owned file before invoking it.
# Its directory contains the markers both on the host and at the sandbox mount.
control_root="$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)" || exit 125
marker_id="${1:-}"
encoded_command="${2:-}"

case "$marker_id" in
    *[!0-9a-f]*|"")
        exit 125
        ;;
esac
if [ "${#marker_id}" -ne 64 ] || [ ! -d "$control_root" ] || [ ! -r "$control_root" ]; then
    exit 125
fi

command_text="$(printf '%s' "$encoded_command" | base64 -d 2>/dev/null)" || exit 125
marker="$control_root/$marker_id.cancel"

if [ "$(uname -s)" = Darwin ]; then
    # macOS has setsid(2), but no setsid command. Keep the child PID as
    # process-group leader so cancellation also reaches its descendants.
    # Match OpenClaw's noninteractive Bash invocation. macOS sh has different
    # echo semantics and login profiles can change the requested working dir.
    /usr/bin/python3 -c 'import os, sys; os.setsid(); os.execv("/bin/bash", ["bash", "--noprofile", "--norc", "-c", sys.argv[1]])' "$command_text" &
else
    setsid sh -lc "$command_text" &
fi
child_pid=$!

terminate_child() {
    kill -TERM "-$child_pid" 2>/dev/null || kill -TERM "$child_pid" 2>/dev/null || true
    sleep 1
    if kill -0 "-$child_pid" 2>/dev/null; then
        kill -KILL "-$child_pid" 2>/dev/null || kill -KILL "$child_pid" 2>/dev/null || true
    fi
}

trap 'terminate_child; wait "$child_pid" 2>/dev/null; exit 130' HUP INT TERM

while kill -0 "$child_pid" 2>/dev/null; do
    if [ -f "$marker" ]; then
        terminate_child
        wait "$child_pid" 2>/dev/null || true
        exit 130
    fi
    sleep 0.1
done

wait "$child_pid"
exit $?
