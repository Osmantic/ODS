#!/usr/bin/env bash
set -euo pipefail

[[ $# -ge 4 ]] || { echo 'Usage: native-llama-service.sh start|stop|status INSTALL BINARY PID_FILE [ARGS...]' >&2; exit 2; }
action="$1"
install_dir="$2"
binary="$3"
pid_file="$4"
shift 4
case "$action" in start|stop|status) ;; *) exit 2 ;; esac
manager="$install_dir/bin/macos_native_service.py"
[[ -f "$manager" ]] || { echo 'Native service manager is missing; reinstall ODS.' >&2; exit 1; }
if [[ "$action" == start ]]; then
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
fi
exec "${ODS_PYTHON_CMD:-python3}" "$manager" "$action" "$install_dir" "$binary" "$pid_file" "$@"
