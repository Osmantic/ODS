#!/bin/sh
set -u

control_root=/run/pixel-ods-control
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

setsid sh -lc "$command_text" &
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
