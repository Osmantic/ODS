#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

extract_function() {
    local name="$1"
    awk -v signature="${name}()" '
        $0 == signature " {" { in_fn=1 }
        in_fn { print }
        in_fn && $0 == "}" { exit }
    ' "$ROOT/ods-cli"
}

eval "$(extract_function _ods_cli_wait_for_hermes_ready)"

success() { :; }
log() { :; }
log_error() { :; }
sleep() { :; }

sequence_file="$(mktemp)"
trap 'rm -f "$sequence_file" "$sequence_file.next"' EXIT

docker() {
    if [[ "${1:-}" == "inspect" && "${2:-}" == "ods-hermes" ]]; then
        return "${MOCK_CONTAINER_MISSING:-0}"
    fi
    if [[ "${1:-}" == "inspect" && "${2:-}" == "--format" ]]; then
        head -1 "$sequence_file"
        tail -n +2 "$sequence_file" > "$sequence_file.next"
        mv "$sequence_file.next" "$sequence_file"
        return 0
    fi
    return 1
}

MOCK_CONTAINER_MISSING=1
ODS_HERMES_READY_TIMEOUT=1 ODS_HERMES_READY_INTERVAL=1 \
    _ods_cli_wait_for_hermes_ready

MOCK_CONTAINER_MISSING=0
printf '%s\n' starting healthy > "$sequence_file"
ODS_HERMES_READY_TIMEOUT=2 ODS_HERMES_READY_INTERVAL=1 \
    _ods_cli_wait_for_hermes_ready

printf '%s\n' unhealthy > "$sequence_file"
if ODS_HERMES_READY_TIMEOUT=2 ODS_HERMES_READY_INTERVAL=1 \
    _ods_cli_wait_for_hermes_ready; then
    echo "Hermes readiness accepted an unhealthy container" >&2
    exit 1
fi

printf '%s\n' starting starting > "$sequence_file"
if ODS_HERMES_READY_TIMEOUT=2 ODS_HERMES_READY_INTERVAL=1 \
    _ods_cli_wait_for_hermes_ready; then
    echo "Hermes readiness accepted a container that never became healthy" >&2
    exit 1
fi

awk '/cmd_restart\(\)/,/^}/' "$ROOT/ods-cli" \
    | grep -Fq '_ods_cli_wait_for_hermes_ready'

echo "ODS restart Hermes readiness checks passed"
