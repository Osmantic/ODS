#!/usr/bin/env bash
# Exercise the shipped Compose runner with inert Docker and recovery boundaries.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_text="$(sed -n '/^_compose_run_with_summary() {$/,/^}$/p' "$ROOT/ods-cli")"
[[ -n "$source_text" ]]
eval "$source_text"

INSTALL_DIR=/fixture
DOCKER_STUB_MODE=1
ODS_COMPOSE_STARTUP_RETRY_ATTEMPTS=1
ODS_COMPOSE_PORT_RETRY_DELAY=0
_ods_wsl_bind_retry_helper=/fixture/scripts/wsl-bind-recovery.py
_ods_wsl_bind_retry_service=litellm
_ods_wsl_bind_retry_flags=(-f fixture.yml)
events="$(mktemp)"
trap 'rm -f "$events"' EXIT
log() { :; }
success() { :; }
warn() { :; }
log_error() { :; }
_check_docker_access() { :; }
docker() {
    [[ "$1" == compose && "$2" == --progress && "$3" == quiet ]]
    printf 'compose\n' >> "$events"
    attempts=$((attempts + 1))
    if [[ "$scenario" == unrelated ]]; then
        printf 'network unavailable\n' >&2
        return 42
    fi
    if [[ "$scenario" == port && "$attempts" == 1 ]]; then
        printf 'port is already allocated\n' >&2
        return 42
    fi
    if (( attempts == 1 )) || [[ "$scenario" == persistent ]]; then
        printf 'failed to create task: OCI runtime create failed: error mounting "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu/%064d" to rootfs at "/app/config.yaml": no such file or directory\n' 0 >&2
        [[ "$scenario" == mixed ]] && printf 'port is already allocated\n' >&2
        return 42
    fi
}
python3() {
    [[ "$1" == "$_ods_wsl_bind_retry_helper" && "$2" == --install-dir && "$3" == "$INSTALL_DIR" &&
       "$4" == --service && "$5" == litellm ]]
    if [[ "$6" == --check ]]; then
        [[ "$7" == -- && "$8" == -f && "$9" == fixture.yml ]]
        printf 'check\n' >> "$events"
        return "$check_result"
    fi
    [[ "$6" == -- && "$7" == -f && "$8" == fixture.yml ]]
    printf 'repair\n' >> "$events"
    return "$repair_result"
}
run_case() {
    : > "$events"
    attempts=0
    local rc=0
    _compose_run_with_summary 'Starting litellm' -f fixture.yml up -d litellm > /dev/null 2>&1 || rc=$?
    [[ "$rc" == "$expected_rc" && "$(cat "$events")" == "$expected_events" ]]
}

scenario=mount check_result=2 repair_result=0 expected_rc=0
expected_events=$'compose\ncheck\nrepair\ncompose'
run_case
printf 'PASS: proven stale selected bind repairs once and retries Compose\n'

scenario=persistent check_result=2 repair_result=0 expected_rc=42
expected_events=$'compose\ncheck\nrepair\ncompose'
ODS_COMPOSE_STARTUP_RETRY_ATTEMPTS=3
run_case
ODS_COMPOSE_STARTUP_RETRY_ATTEMPTS=1
printf 'PASS: persistent mount failure stops after one guarded retry\n'

scenario=mount check_result=0 repair_result=0 expected_rc=42
expected_events=$'compose\ncheck'
run_case
printf 'PASS: unproven bind performs no recovery or mount retry\n'

scenario=mount check_result=1 repair_result=0 expected_rc=42
expected_events=$'compose\ncheck'
run_case
printf 'PASS: recovery refusal prevents another Compose attempt\n'

scenario=mixed check_result=1 repair_result=0 expected_rc=42
expected_events=$'compose\ncheck'
run_case
printf 'PASS: mount refusal also blocks a mixed port-conflict retry\n'

scenario=mount check_result=2 repair_result=1 expected_rc=42
expected_events=$'compose\ncheck\nrepair'
run_case
printf 'PASS: refused recovery does not repeat Compose\n'

scenario=unrelated check_result=2 repair_result=0 expected_rc=42
expected_events='compose'
run_case
printf 'PASS: unrelated Compose failure does not call recovery\n'

scenario=port check_result=2 repair_result=0 expected_rc=0
expected_events=$'compose\ncompose'
run_case
printf 'PASS: ordinary port conflict keeps its bounded retry\n'

scenario=mount check_result=2 repair_result=0 expected_rc=42
expected_events='compose'
_ods_wsl_bind_retry_helper=''
run_case
printf 'PASS: non-WSL starts never enter WSL recovery\n'
