#!/usr/bin/env bash
# Regression coverage for macOS CLI compose-mode and inference routing.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/install"
MOCK_BIN="$TMP_DIR/bin"
CURL_LOG="$TMP_DIR/curl.log"
DOCKER_LOG="$TMP_DIR/docker.log"
RESOLVER_LOG="$TMP_DIR/resolver.log"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/installers/macos" "$MOCK_BIN"
touch "$INSTALL_DIR/docker-compose.base.yml"
touch "$INSTALL_DIR/docker-compose.cloud.yml"
touch "$INSTALL_DIR/installers/macos/docker-compose.macos.yml"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

assert_log_contains() {
    local file="$1" expected="$2" message="$3"
    grep -Fq -- "$expected" "$file" || fail "$message"
}

assert_log_excludes() {
    local file="$1" unexpected="$2" message="$3"
    if grep -Fq -- "$unexpected" "$file"; then
        fail "$message"
    fi
}

cat > "$MOCK_BIN/jq" <<'MOCK_JQ'
#!/usr/bin/env bash
if [[ "${1:-}" == "-n" ]]; then
    printf '%s\n' '{"model":"default","messages":[{"role":"user","content":"test"}]}'
else
    printf '%s\n' 'mock answer'
fi
MOCK_JQ

cat > "$MOCK_BIN/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
for arg in "$@"; do
    if [[ "$arg" == "-w" ]]; then
        printf '200'
        exit 0
    fi
done
printf '%s\n' '{"choices":[{"message":{"content":"mock answer"}}]}'
MOCK_CURL

cat > "$MOCK_BIN/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
exit 0
MOCK_DOCKER
chmod +x "$MOCK_BIN/jq" "$MOCK_BIN/curl" "$MOCK_BIN/docker"

run_cli() {
    PATH="$MOCK_BIN:$PATH" \
    ODS_HOME="$INSTALL_DIR" \
    CURL_LOG="$CURL_LOG" \
    DOCKER_LOG="$DOCKER_LOG" \
    RESOLVER_LOG="$RESOLVER_LOG" \
        bash "$CLI" "$@"
}

write_local_env() {
    cat > "$INSTALL_DIR/.env" <<'LOCAL_ENV'
ODS_MODE=local
BIND_ADDRESS=192.168.106.1
ODS_NATIVE_LLAMA_PORT=9090
LOCAL_ENV
    printf '%s\n' '-f docker-compose.base.yml -f installers/macos/docker-compose.macos.yml' \
        > "$INSTALL_DIR/.compose-flags"
}

write_cloud_env() {
    cat > "$INSTALL_DIR/.env" <<'CLOUD_ENV'
ODS_MODE=cloud
BIND_ADDRESS=192.168.106.1
LITELLM_PORT=4010
LITELLM_KEY=sk-test-cloud-key
CLOUD_ENV
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cloud.yml' \
        > "$INSTALL_DIR/.compose-flags"
}

write_local_env
: > "$CURL_LOG"
run_cli chat "local route" >/dev/null
assert_log_contains "$CURL_LOG" \
    'http://192.168.106.1:9090/v1/chat/completions' \
    "local chat did not use the configured native bind and port"
assert_log_excludes "$CURL_LOG" 'Authorization: Bearer' \
    "local native chat unexpectedly sent a LiteLLM credential"
pass "local chat follows the configured native bind"

write_cloud_env
: > "$CURL_LOG"
run_cli chat "cloud route" >/dev/null
assert_log_contains "$CURL_LOG" \
    'http://192.168.106.1:4010/v1/chat/completions' \
    "cloud chat did not use the host-published LiteLLM port"
assert_log_contains "$CURL_LOG" 'Authorization: Bearer sk-test-cloud-key' \
    "cloud chat did not authenticate with LITELLM_KEY"
pass "cloud chat uses authenticated LiteLLM"

grep -v '^LITELLM_KEY=' "$INSTALL_DIR/.env" > "$TMP_DIR/cloud-no-key.env"
mv "$TMP_DIR/cloud-no-key.env" "$INSTALL_DIR/.env"
: > "$CURL_LOG"
if run_cli chat "missing key" >/dev/null 2>&1; then
    fail "cloud chat succeeded without LITELLM_KEY"
fi
[[ ! -s "$CURL_LOG" ]] || fail "cloud chat contacted LiteLLM without a key"
pass "cloud chat fails before transport when authentication is unavailable"

write_local_env
: > "$CURL_LOG"
run_cli status >/dev/null
assert_log_contains "$CURL_LOG" 'http://192.168.106.1:9090/health' \
    "local status did not probe the configured native bind"
pass "local status probes the configured native inference route"

write_cloud_env
: > "$CURL_LOG"
cloud_status="$(run_cli status)"
assert_log_contains "$CURL_LOG" 'http://192.168.106.1:4010/v1/models' \
    "cloud status did not probe authenticated LiteLLM"
assert_log_contains "$CURL_LOG" 'Authorization: Bearer sk-test-cloud-key' \
    "cloud status did not authenticate its LiteLLM probe"
grep -Fq 'LiteLLM cloud gateway' <<< "$cloud_status" \
    || fail "cloud status did not identify the active cloud backend"
if grep -Fq 'native Metal): not running' <<< "$cloud_status"; then
    fail "cloud status incorrectly reported intentionally stopped native llama as a failure"
fi
pass "cloud status reports and authenticates the LiteLLM route"

mkdir -p "$INSTALL_DIR/scripts"
cat > "$INSTALL_DIR/scripts/resolve-compose-stack.sh" <<'MOCK_RESOLVER'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$RESOLVER_LOG"
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cloud.yml'
MOCK_RESOLVER
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
rm -f "$INSTALL_DIR/.compose-flags"
: > "$RESOLVER_LOG"
run_cli status >/dev/null
assert_log_contains "$RESOLVER_LOG" '--ods-mode cloud' \
    "macOS CLI fallback resolver did not preserve ODS_MODE=cloud"
pass "fallback compose resolution preserves the persisted ODS mode"

rm -f "$INSTALL_DIR/scripts/resolve-compose-stack.sh" "$INSTALL_DIR/.compose-flags"
: > "$DOCKER_LOG"
run_cli status >/dev/null
assert_log_contains "$DOCKER_LOG" '-f docker-compose.cloud.yml ps' \
    "resolver-less cloud fallback selected the local macOS overlay"
pass "resolver-less fallback remains in cloud mode"

resolver_fixture="$TMP_DIR/resolver-fixture"
mkdir -p "$resolver_fixture/data/generated"
cat > "$resolver_fixture/docker-compose.base.yml" <<'BASE_COMPOSE'
services:
  open-webui:
    image: example.invalid/open-webui:test
BASE_COMPOSE
cat > "$resolver_fixture/docker-compose.cloud.yml" <<'CLOUD_COMPOSE'
services:
  open-webui:
    environment:
      OPENAI_API_BASE_URL: http://litellm:4000/v1
CLOUD_COMPOSE
cat > "$resolver_fixture/data/generated/docker-compose.macos-cloud-auth.yml" <<'AUTH_OVERLAY'
services:
  open-webui:
    environment:
      OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"
AUTH_OVERLAY
resolved_flags="$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$resolver_fixture" \
    --tier AP_BASE \
    --gpu-backend apple \
    --gpu-count 1 \
    --ods-mode cloud)"
resolved_flags="${resolved_flags//\\//}"
[[ "$resolved_flags" == *'-f docker-compose.base.yml -f docker-compose.cloud.yml -f data/generated/docker-compose.macos-cloud-auth.yml' ]] \
    || fail "cache rebuild dropped or misordered the generated macOS cloud-auth overlay: $resolved_flags"
pass "cloud cache rebuild preserves the generated client-auth overlay"

echo "[OK] macOS CLI mode-aware routing contract holds"
