#!/usr/bin/env bash
# Regression: an opt-in local image rebuild must stop lifecycle commands when
# Docker cannot produce the requested images.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
cli_path="$root_dir/ods-cli"
tmp_dir="$(mktemp -d)"
docker_log="$tmp_dir/docker.log"
trap 'rm -rf "$tmp_dir"' EXIT

function_source="$(awk '/^_ods_cli_rebuild_images\(\)/,/^}/' "$cli_path")"
[[ -n "$function_source" ]] || {
    printf '[FAIL] could not extract _ods_cli_rebuild_images\n' >&2
    exit 1
}

log() { :; }
_check_docker_access() { :; }
error() {
    printf '%s\n' "$1" >&2
    exit 1
}
docker() {
    printf '%s\n' "$*" >> "$docker_log"
    if [[ " $* " == *" config --services "* ]]; then
        printf '%s\n' dashboard dashboard-api model-router remote-provider-egress \
            remote-provider-ssh-tunnel ape token-spy privacy-shield brave-search
        return 0
    fi
    return "${TEST_DOCKER_BUILD_RC:-0}"
}

eval "$function_source"

set +e
failure_output="$(TEST_DOCKER_BUILD_RC=23 _ods_cli_rebuild_images -f docker-compose.base.yml 2>&1)"
failure_rc=$?
set -e

[[ "$failure_rc" -ne 0 ]] || {
    printf '[FAIL] rebuild helper returned success after docker compose build failed\n' >&2
    exit 1
}
grep -q 'Failed to rebuild locally-built images; services were not changed' <<< "$failure_output" || {
    printf '[FAIL] rebuild helper did not surface an actionable failure\n%s\n' "$failure_output" >&2
    exit 1
}

: > "$docker_log"
TEST_DOCKER_BUILD_RC=0 _ods_cli_rebuild_images -f docker-compose.base.yml
grep -q '^compose -f docker-compose.base.yml build --no-cache ' "$docker_log" || {
    printf '[FAIL] successful rebuild did not invoke the expected Compose build boundary\n' >&2
    cat "$docker_log" >&2
    exit 1
}

for command_name in cmd_start cmd_restart cmd_update; do
    command_source="$(awk "/^${command_name}\\(\\)/,/^}/" "$cli_path")"
    grep -q '_ods_cli_rebuild_images' <<< "$command_source" || {
        printf '[FAIL] %s no longer routes --rebuild-images through the guarded helper\n' "$command_name" >&2
        exit 1
    }
done

printf '[PASS] CLI image rebuild failures stop start, restart, and update\n'
