#!/usr/bin/env bash
# Regression: `ods update` must not fail under set -e while counting services.

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-cli requires Bash 4+\n'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
ods_cli="$root_dir/ods-cli"
tmp_dir="$(mktemp -d)"
install_dir="$tmp_dir/install"
bin_dir="$tmp_dir/bin"
docker_log="$tmp_dir/docker.log"
pull_count_file="$tmp_dir/pull-count"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$install_dir/data" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
cp "$root_dir/manifest.json" "$install_dir/manifest.json"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"

cat > "$install_dir/.env" <<'ENV'
ODS_VERSION=2.5.3
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
SHIELD_API_KEY=test-shield-key
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
ENV

cat > "$install_dir/ods-update.sh" <<'UPDATE'
#!/usr/bin/env bash
exit 0
UPDATE
chmod +x "$install_dir/ods-update.sh"

cat > "$bin_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${TEST_DOCKER_LOG:?}"

if [[ "${1:-}" == "info" ]]; then
    [[ "$*" == *NCPU* ]] && printf '16\n'
    exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
    shift
    args=("$@")
    joined=" ${args[*]} "
    if [[ "$joined" == *" pull "* ]]; then
        count_file="${TEST_DOCKER_PULL_COUNT:-}"
        count=0
        [[ -n "$count_file" && -f "$count_file" ]] && count="$(cat "$count_file")"
        count=$((count + 1))
        [[ -n "$count_file" ]] && printf '%s\n' "$count" > "$count_file"
        if [[ "${TEST_DOCKER_PULL_FAIL_ONCE:-}" == "1" && "$count" == "1" ]]; then
            printf '%s\n' 'Error response from daemon: Head "https://registry-1.docker.io/v2/n8nio/n8n/manifests/2.6.4": Get "https://auth.docker.io/token?scope=repository%3An8nio%2Fn8n%3Apull&service=registry.docker.io": context deadline exceeded' >&2
            exit 1
        fi
        exit 0
    fi
    for ((i = 0; i < ${#args[@]}; i++)); do
        if [[ "${args[$i]}" == "config" && "${args[$((i + 1))]:-}" == "--services" ]]; then
            printf '%s\n' dashboard dashboard-api aider
            exit 0
        fi
        if [[ "${args[$i]}" == "ps" ]]; then
            joined=" ${args[*]} "
            if [[ "$joined" == *" --services "* && "$joined" == *" --status running "* ]]; then
                printf '%s\n' dashboard dashboard-api
                exit 0
            fi
            if [[ "$joined" == *" --all -q aider "* ]]; then
                printf '%s\n' aider-container-id
                exit 0
            fi
        fi
    done
    exit 0
fi

if [[ "${1:-}" == "inspect" ]]; then
    if [[ "${*: -1}" == "aider-container-id" ]]; then
        printf '%s\n' 'exited 0'
        exit 0
    fi
    exit 1
fi

if [[ "${1:-}" == "ps" ]]; then
    name=""
    prev=""
    for arg in "$@"; do
        if [[ "$prev" == "--filter" && "$arg" == name=* ]]; then
            name="${arg#name=}"
        fi
        prev="$arg"
    done
    [[ -n "$name" ]] && printf '%s\n' "$name"
    exit 0
fi

exit 0
DOCKER

cat > "$bin_dir/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP

chmod +x "$bin_dir/docker" "$bin_dir/sleep"
: > "$docker_log"

PATH="$bin_dir:$PATH" \
ODS_HOME="$install_dir" \
NO_COLOR=1 \
TEST_DOCKER_LOG="$docker_log" \
TEST_DOCKER_PULL_COUNT="$pull_count_file" \
TEST_DOCKER_PULL_FAIL_ONCE=1 \
    "$BASH" "$ods_cli" update --force > "$tmp_dir/update.out" 2>&1 || {
        cat "$tmp_dir/update.out" >&2
        exit 1
    }

grep -q 'Update complete' "$tmp_dir/update.out" || {
    cat "$tmp_dir/update.out" >&2
    printf '[FAIL] update did not reach completion\n' >&2
    exit 1
}

grep -q -- 'pull --ignore-buildable' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] update did not skip local-build images during compose pull\n' >&2
    exit 1
}

if [[ "$(cat "$pull_count_file")" != "2" ]]; then
    cat "$tmp_dir/update.out" >&2
    cat "$docker_log" >&2
    printf '[FAIL] update did not retry transient compose pull failure\n' >&2
    exit 1
fi

grep -q 'Docker registry pull hit a transient network error; retrying' "$tmp_dir/update.out" || {
    cat "$tmp_dir/update.out" >&2
    printf '[FAIL] update did not warn before retrying transient compose pull failure\n' >&2
    exit 1
}

grep -q -- 'config --services' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] update did not inspect the active compose stack\n' >&2
    exit 1
}

grep -q -- 'ps --services --status running' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] update did not verify running compose services\n' >&2
    exit 1
}

printf '[PASS] ods update verifies active compose services under set -e\n'
