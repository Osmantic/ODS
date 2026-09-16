#!/usr/bin/env bash
# Regression: Linux/macOS `restart` must replace running containers so the
# process is actually restarted and receives current Compose environment.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
tmp_dir="$(mktemp -d)"
install_dir="$tmp_dir/install"
bin_dir="$tmp_dir/bin"
docker_log="$tmp_dir/docker.log"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$install_dir/data" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
printf '%s\n' \
    'ODS_VERSION=2.6.0' \
    'ODS_MODE=local' \
    'GPU_BACKEND=apple' \
    'GPU_COUNT=1' \
    'TIER=1' \
    'LLAMA_CPU_LIMIT=8.0' \
    'LLAMA_CPU_RESERVATION=2.0' \
    'HERMES_CPU_LIMIT=4.0' \
    'HERMES_CPU_RESERVATION=0.5' \
    > "$install_dir/.env"

printf '%s\n' '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf '\''%s\n'\'' "$*" >> "${TEST_DOCKER_LOG:?}"' \
    'case "${1:-}" in' \
    '    info)' \
    '        [[ "$*" == *NCPU* ]] && printf '\''16\n'\''' \
    '        exit 0' \
    '        ;;' \
    '    ps) exit 0 ;;' \
    '    compose)' \
    '        [[ " $* " == *" up -d "* ]] && exit 0' \
    '        ;;' \
    'esac' \
    'printf '\''unexpected docker invocation: %s\n'\'' "$*" >&2' \
    'exit 1' \
    > "$bin_dir/docker"
chmod +x "$bin_dir/docker"

run_restart_contract() {
    local platform="$1"
    local cli="$2"
    local output="$tmp_dir/${platform}.out"
    : > "$docker_log"

    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    TEST_DOCKER_LOG="$docker_log" \
        "$BASH" "$cli" restart dashboard > "$output" 2>&1 || {
            sed -n '1,200p' "$output" >&2
            return 1
        }
    grep -Eq 'compose .*docker-compose.base.yml up -d --force-recreate --no-build --pull never dashboard$' "$docker_log" || {
        sed -n '1,200p' "$docker_log" >&2
        printf '[FAIL] %s single-service restart did not replace the container from its pinned image\n' "$platform" >&2
        return 1
    }

    : > "$docker_log"
    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    TEST_DOCKER_LOG="$docker_log" \
        "$BASH" "$cli" restart > "$output" 2>&1 || {
            sed -n '1,200p' "$output" >&2
            return 1
        }
    grep -Eq 'compose .*docker-compose.base.yml up -d --force-recreate --no-build --pull never$' "$docker_log" || {
        sed -n '1,200p' "$docker_log" >&2
        printf '[FAIL] %s all-service restart did not replace containers from pinned images\n' "$platform" >&2
        return 1
    }
}

run_restart_contract linux "$root_dir/ods-cli"
run_restart_contract macos "$root_dir/installers/macos/ods-macos.sh"

printf '[PASS] Linux and macOS restart recreate env-backed containers\n'
