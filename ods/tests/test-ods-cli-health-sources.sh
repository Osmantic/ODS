#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

extract_function() {
    sed -n "/^${1}() {/,/^}/p" "$ODS_CLI"
}

eval "$(extract_function _ods_cli_service_health)"

declare -A SERVICE_HEALTH_SOURCES=()
declare -A SERVICE_HEALTH_TIMEOUTS=()

DOCKER_CONTAINER_ID=""
DOCKER_INSPECT_RESULT=""
CURL_RESULT=1

docker() {
    if [[ "$1" == "compose" ]]; then
        [[ "$2" == "-f" && "$3" == "compose.yaml" && "$4" == "ps" \
            && "$5" == "-q" && "$6" == "internal" ]] || return 1
        printf '%s\n' "$DOCKER_CONTAINER_ID"
    elif [[ "$1" == "inspect" ]]; then
        printf '%s\n' "$DOCKER_INSPECT_RESULT"
    else
        return 1
    fi
}

curl() {
    [[ "$*" == *"http://127.0.0.1:8080/health"* ]] || return 1
    return "$CURL_RESULT"
}

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

SERVICE_HEALTH_SOURCES[internal]="container"
DOCKER_CONTAINER_ID="container-id"
DOCKER_INSPECT_RESULT="running healthy"
[[ "$(_ods_cli_service_health internal 0 /health -f compose.yaml)" == "healthy" ]] \
    || fail "healthy container healthcheck was not trusted"
echo "[PASS] container-authoritative service reports healthy"

DOCKER_INSPECT_RESULT="running unhealthy"
[[ "$(_ods_cli_service_health internal 0 /health -f compose.yaml)" == "unhealthy" ]] \
    || fail "unhealthy container healthcheck was trusted"
echo "[PASS] unhealthy container healthcheck fails closed"

DOCKER_CONTAINER_ID=""
[[ "$(_ods_cli_service_health internal 0 /health -f compose.yaml)" == "unhealthy" ]] \
    || fail "missing internal container was treated as healthy"
echo "[PASS] missing internal container fails closed"

DOCKER_CONTAINER_ID=$'first-container\nsecond-container'
[[ "$(_ods_cli_service_health internal 0 /health -f compose.yaml)" == "unhealthy" ]] \
    || fail "ambiguous compose output was treated as healthy"
echo "[PASS] multiple container IDs fail closed"

SERVICE_HEALTH_TIMEOUTS[http-service]=7
CURL_RESULT=0
[[ "$(_ods_cli_service_health http-service 8080 /health)" == "healthy" ]] \
    || fail "default HTTP health path regressed"
echo "[PASS] HTTP health remains the default"
