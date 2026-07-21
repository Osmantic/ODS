#!/usr/bin/env bash
# Regression: macOS `ods-macos.sh update` skips local-build images and retries
# transient Docker registry pull failures.

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-macos.sh requires Bash 4+\n'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
macos_cli="$root_dir/installers/macos/ods-macos.sh"
tmp_dir="$(mktemp -d)"
install_dir="$tmp_dir/install"
bin_dir="$tmp_dir/bin"
docker_log="$tmp_dir/docker.log"
pull_count_file="$tmp_dir/pull-count"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$install_dir/data" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"

cat > "$install_dir/.env" <<'ENV'
ODS_VERSION=2.5.3
ODS_MODE=local
GPU_BACKEND=apple
GPU_COUNT=1
TIER=1
SHIELD_API_KEY=test-shield-key
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
TTS_CPU_LIMIT=8.0
TTS_CPU_RESERVATION=2.0
WHISPER_CPU_LIMIT=4.0
WHISPER_CPU_RESERVATION=1.0
HERMES_CPU_LIMIT=4.0
HERMES_CPU_RESERVATION=0.5
COMFYUI_CPU_LIMIT=16.0
COMFYUI_CPU_RESERVATION=2.0
ENV

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
            printf '%s\n' 'Error response from daemon: redis:7.4.8-alpine: failed to authorize: failed to fetch anonymous token: context deadline exceeded' >&2
            exit 1
        fi
        exit 0
    fi
    if [[ "$joined" == *" up "* ]]; then
        exit 0
    fi
    if [[ "$joined" == *" ps "* ]]; then
        printf '%s\n' 'ods-dashboard Up 10 seconds 127.0.0.1:3001->3001/tcp'
        exit 0
    fi
fi

exit 0
DOCKER

cat > "$bin_dir/curl" <<'CURL'
#!/usr/bin/env bash
printf '200\n'
CURL

cat > "$bin_dir/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP

chmod +x "$bin_dir/docker" "$bin_dir/curl" "$bin_dir/sleep"
: > "$docker_log"

PATH="$bin_dir:$PATH" \
ODS_HOME="$install_dir" \
NO_COLOR=1 \
TEST_DOCKER_LOG="$docker_log" \
TEST_DOCKER_PULL_COUNT="$pull_count_file" \
TEST_DOCKER_PULL_FAIL_ONCE=1 \
ODS_COMPOSE_PULL_RETRY_DELAY_1=0 \
ODS_COMPOSE_PULL_RETRY_DELAY_2=0 \
ODS_COMPOSE_PULL_RETRY_DELAY_N=0 \
    "$BASH" "$macos_cli" update > "$tmp_dir/update.out" 2>&1 || {
        cat "$tmp_dir/update.out" >&2
        exit 1
    }

grep -q 'Update complete' "$tmp_dir/update.out" || {
    cat "$tmp_dir/update.out" >&2
    printf '[FAIL] macOS update did not reach completion\n' >&2
    exit 1
}

grep -q -- 'pull --ignore-buildable' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] macOS update did not skip local-build images during compose pull\n' >&2
    exit 1
}

if [[ "$(cat "$pull_count_file")" != "2" ]]; then
    cat "$tmp_dir/update.out" >&2
    cat "$docker_log" >&2
    printf '[FAIL] macOS update did not retry transient compose pull failure\n' >&2
    exit 1
fi

grep -q 'Docker registry pull hit a transient network error; retrying' "$tmp_dir/update.out" || {
    cat "$tmp_dir/update.out" >&2
    printf '[FAIL] macOS update did not warn before retrying transient compose pull failure\n' >&2
    exit 1
}

grep -q -- 'up -d --force-recreate' "$docker_log" || {
    cat "$docker_log" >&2
    printf '[FAIL] macOS update did not recreate containers after pull\n' >&2
    exit 1
}

printf '[PASS] macOS update retries transient compose pull failures\n'
