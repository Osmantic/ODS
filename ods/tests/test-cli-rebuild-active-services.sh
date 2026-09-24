#!/usr/bin/env bash
# Regression: --rebuild-images must target the active Compose stack, including
# newer build-only services, without naming disabled optional services.

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
success() { :; }
warn() { :; }
_check_docker_access() { :; }
error() {
    printf '%s\n' "$1" >&2
    exit 1
}
docker() {
    printf '%s\n' "$*" >> "$docker_log"
    if [[ " $* " == *" config --services "* ]]; then
        [[ "${TEST_CONFIG_FAIL:-}" != "1" ]] || return 24
        printf '%s\n' "${TEST_ENABLED_SERVICES:-}"
    fi
}

eval "$function_source"

TEST_ENABLED_SERVICES=$'dashboard\nmodel-router\nbrave-search' \
    _ods_cli_rebuild_images -f docker-compose.base.yml

grep -q '^compose -f docker-compose.base.yml config --services$' "$docker_log" || {
    printf '[FAIL] rebuild helper did not resolve the active Compose services\n' >&2
    cat "$docker_log" >&2
    exit 1
}
grep -q '^compose -f docker-compose.base.yml build --no-cache dashboard model-router brave-search$' "$docker_log" || {
    printf '[FAIL] rebuild helper did not select the exact active local-build services\n' >&2
    cat "$docker_log" >&2
    exit 1
}

: > "$docker_log"
TEST_ENABLED_SERVICES=n8n _ods_cli_rebuild_images -f docker-compose.base.yml
if grep -q ' build ' "$docker_log"; then
    printf '[FAIL] rebuild helper invoked Compose build with no active local services\n' >&2
    cat "$docker_log" >&2
    exit 1
fi

: > "$docker_log"
set +e
failure_output="$(TEST_CONFIG_FAIL=1 _ods_cli_rebuild_images -f docker-compose.base.yml 2>&1)"
failure_rc=$?
set -e
[[ "$failure_rc" -ne 0 ]] || {
    printf '[FAIL] rebuild helper continued after Compose service resolution failed\n' >&2
    exit 1
}
grep -q 'Could not resolve enabled Compose services' <<< "$failure_output" || {
    printf '[FAIL] rebuild helper did not explain the Compose resolution failure\n' >&2
    exit 1
}
if grep -q ' build ' "$docker_log"; then
    printf '[FAIL] rebuild helper guessed a build list after resolution failed\n' >&2
    cat "$docker_log" >&2
    exit 1
fi

printf '[PASS] CLI rebuilds exactly the active local-image services\n'
