#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/postflight-docker-context.sh"

account_has_docker=true
current_has_docker=false
daemon_healthy=true
sg_calls=0
direct_calls=0
last_path=""
last_arg=""

id() {
    if [[ "$1" == -un ]]; then
        printf 'test-user\n'
    elif [[ "$1" == -nG && $# -eq 1 ]]; then
        if $current_has_docker; then printf 'test-user docker\n'; else printf 'test-user\n'; fi
    elif [[ "$1" == -nG && "$2" == test-user ]]; then
        if $account_has_docker; then printf 'test-user docker\n'; else printf 'test-user\n'; fi
    else
        return 1
    fi
}

sg() {
    [[ "$1" == docker && "$2" == -c ]]
    ((sg_calls+=1))
    last_path="$ODS_POSTFLIGHT_SCRIPT_PATH"
    last_arg="${ODS_POSTFLIGHT_SCRIPT_ARG-}"
    $daemon_healthy
}

bash() {
    ((direct_calls+=1))
    last_path="$1"
    last_arg="${2-}"
    $daemon_healthy
}

# A newly added account group is enough to select a non-elevated fresh-group
# subprocess for both checks. Paths and arguments stay literal, even with
# spaces or shell punctuation.
ods_postflight_run_docker_check '/tmp/ODS preflight.sh'
[[ "$sg_calls" -eq 1 && "$direct_calls" -eq 0 && "$last_path" == '/tmp/ODS preflight.sh' ]]
ods_postflight_run_docker_check '/tmp/extension check.sh' '/tmp/ODS data; untouched'
[[ "$sg_calls" -eq 2 && "$direct_calls" -eq 0 && "$last_arg" == '/tmp/ODS data; untouched' ]]

# Once the shell has docker membership, use it directly; do not route through
# sg merely because the account belongs to docker.
current_has_docker=true
ods_postflight_run_docker_check '/tmp/ODS preflight.sh'
[[ "$sg_calls" -eq 2 && "$direct_calls" -eq 1 ]]

# Neither a missing daemon nor an unconfigured docker group becomes a pass.
current_has_docker=false
daemon_healthy=false
if ods_postflight_run_docker_check '/tmp/ODS preflight.sh'; then exit 1; fi
[[ "$sg_calls" -eq 3 ]]
account_has_docker=false
if ods_postflight_run_docker_check '/tmp/ODS preflight.sh'; then exit 1; fi
[[ "$direct_calls" -eq 2 ]]

grep -q 'ods_postflight_run_docker_check "$SCRIPT_DIR/ods-preflight.sh"' \
    "$ROOT/installers/phases/13-summary.sh"
grep -q 'ods_postflight_run_docker_check "$SCRIPT_DIR/scripts/extension-runtime-check.sh"' \
    "$ROOT/installers/phases/13-summary.sh"
printf 'PASS: post-install checks refresh stale docker group without sudo or false green\n'
