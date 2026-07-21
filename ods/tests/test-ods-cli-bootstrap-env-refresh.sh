#!/usr/bin/env bash
# Regression: start/restart must not reuse bootstrap model variables after
# waiting for a background full-model promotion to finish.

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
compose_log="$tmp_dir/compose.log"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$install_dir/data" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"

cat > "$bin_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "info" ]]; then
    if [[ "$*" == *NCPU* ]]; then printf '16\n'; fi
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    printf '%s|%s|%s|%s|%s\n' \
        "${TEST_ACTION:?}" "${GGUF_FILE-}" "${LLM_MODEL-}" \
        "${MAX_CONTEXT-}" "${CTX_SIZE-}" >> "${TEST_COMPOSE_LOG:?}"
    exit 0
fi
if [[ "${1:-}" == "ps" ]]; then exit 0; fi
exit 0
DOCKER

cat > "$bin_dir/sleep" <<'SLEEP'
#!/usr/bin/env bash
set -euo pipefail
cat > "${TEST_INSTALL_DIR:?}/.env.next" <<'ENV'
ODS_MODE=local
TIER=1
GPU_BACKEND=cpu
GGUF_FILE=Full.gguf
LLM_MODEL=full
MAX_CONTEXT=131072
CTX_SIZE=131072
ENV
mv "${TEST_INSTALL_DIR}/.env.next" "${TEST_INSTALL_DIR}/.env"
printf '{"status":"complete"}\n' > "${TEST_INSTALL_DIR}/data/bootstrap-status.json.next"
mv "${TEST_INSTALL_DIR}/data/bootstrap-status.json.next" \
    "${TEST_INSTALL_DIR}/data/bootstrap-status.json"
SLEEP

chmod +x "$bin_dir/docker" "$bin_dir/sleep"

run_case() {
    local action="$1"
    cat > "$install_dir/.env" <<'ENV'
ODS_MODE=local
TIER=1
GPU_BACKEND=cpu
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap
MAX_CONTEXT=65536
CTX_SIZE=65536
ENV
    printf '{"status":"swapping"}\n' > "$install_dir/data/bootstrap-status.json"

    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_SECONDS=5 \
    ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_INTERVAL=1 \
    TEST_ACTION="$action" \
    TEST_COMPOSE_LOG="$compose_log" \
    TEST_INSTALL_DIR="$install_dir" \
        "$BASH" "$ods_cli" "$action" >/dev/null
}

: > "$compose_log"
run_case restart
run_case start

expected_restart='restart|Full.gguf|full|131072|131072'
expected_start='start|Full.gguf|full|131072|131072'
grep -Fxq "$expected_restart" "$compose_log"
grep -Fxq "$expected_start" "$compose_log"
if grep -Fq 'Bootstrap.gguf' "$compose_log"; then
    printf '[FAIL] compose inherited stale bootstrap model variables\n' >&2
    cat "$compose_log" >&2
    exit 1
fi

printf '[PASS] ods start/restart reload model env after bootstrap wait\n'
