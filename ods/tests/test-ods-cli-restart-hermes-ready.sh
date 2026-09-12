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
eval "$(extract_function _ods_cli_wait_for_hermes_ready_with_retry)"

success() { :; }
log() { :; }
log_error() { :; }
sleep() { :; }
_ods_cli_refresh_soul() { :; }

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
    | grep -Fq '_ods_cli_wait_for_hermes_ready_with_retry'

ready_calls=0
compose_calls=0
_ods_cli_wait_for_hermes_ready() {
    ready_calls=$((ready_calls + 1))
    (( ready_calls > 1 ))
}
docker() {
    [[ "${1:-}" == "inspect" && "${2:-}" == "--format" ]] || return 1
    printf '%s\n' unhealthy
}
_compose_run_with_summary() {
    compose_calls=$((compose_calls + 1))
    [[ "$*" == *"up -d --force-recreate --no-build --pull never hermes"* ]]
}
_ods_cli_wait_for_hermes_ready_with_retry -f compose.yaml
[[ "$ready_calls" -eq 2 ]]
[[ "$compose_calls" -eq 1 ]]

ready_calls=0
compose_calls=0
_ods_cli_wait_for_hermes_ready() {
    ready_calls=$((ready_calls + 1))
    return 1
}
docker() {
    [[ "${1:-}" == "inspect" && "${2:-}" == "--format" ]] || return 1
    printf '%s\n' exited
}
if _ods_cli_wait_for_hermes_ready_with_retry -f compose.yaml; then
    echo "Hermes retry accepted a non-unhealthy terminal state" >&2
    exit 1
fi
[[ "$ready_calls" -eq 1 ]]
[[ "$compose_calls" -eq 0 ]]

ready_calls=0
compose_calls=0
_ods_cli_wait_for_hermes_ready() {
    ready_calls=$((ready_calls + 1))
    return 1
}
docker() {
    [[ "${1:-}" == "inspect" && "${2:-}" == "--format" ]] || return 1
    printf '%s\n' unhealthy
}
if _ods_cli_wait_for_hermes_ready_with_retry -f compose.yaml; then
    echo "Hermes retry accepted repeated unhealthy readiness" >&2
    exit 1
fi
[[ "$ready_calls" -eq 2 ]]
[[ "$compose_calls" -eq 1 ]]

echo "ODS restart Hermes readiness checks passed"
