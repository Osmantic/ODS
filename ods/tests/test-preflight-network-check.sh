#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/installers/phases/01-preflight.sh"

grep -q '_phase01_check_required_network()' "$SOURCE"
grep -q 'OFFLINE_MODE:-false' "$SOURCE"
grep -q -- '--connect-timeout 5 --max-time 10' "$SOURCE"
grep -q -- "-w '%{http_code}'" "$SOURCE"
grep -q "Could not reach \${target_name}" "$SOURCE"
grep -q 'GitHub|https://github.com' "$SOURCE"
grep -q 'Docker Hub|https://registry-1.docker.io/v2/' "$SOURCE"

function_source="$(awk '
    /^_phase01_check_required_network\(\)/ { capture=1 }
    capture { print }
    capture && /^}/ { exit }
' "$SOURCE")"
[[ -n "$function_source" ]]
eval "$function_source"

run_fixture() (
    local github_status="$1" docker_status="$2" transport_failure="${3:-false}"
    export OFFLINE_MODE=false
    # These mocks are invoked by the function extracted and evaluated above;
    # ShellCheck cannot resolve that dynamic call graph.
    # shellcheck disable=SC2317
    curl() {
        local url="${*: -1}"
        [[ "$transport_failure" == "false" ]] || return 7
        if [[ "$url" == "https://github.com" ]]; then
            printf '%s' "$github_status"
        else
            printf '%s' "$docker_status"
        fi
    }
    # shellcheck disable=SC2317
    error() { printf 'error: %s\n' "$*" >&2; exit 97; }
    # shellcheck disable=SC2317
    log() { :; }
    _phase01_check_required_network
)

run_fixture 200 401
if run_fixture 200 503 > /dev/null 2>&1; then
    echo '[FAIL] Docker Hub 503 was accepted as reachable' >&2
    exit 1
fi
if run_fixture 200 401 true > /dev/null 2>&1; then
    echo '[FAIL] transport failure was accepted as reachable' >&2
    exit 1
fi

echo '[PASS] Phase 01 network preflight is bounded and offline-aware'
