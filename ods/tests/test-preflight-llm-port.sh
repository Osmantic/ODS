#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: ods-preflight.sh must probe the llama-server host port —
# canonical external default 11434 (config/ports.json), not the
# container-internal 8080. With no OLLAMA_PORT in .env the old
# ${LLAMA_SERVER_PORT:-8080} fallback reported a healthy install as
# "No LLM endpoint found".
#
# The fixture stubs curl (serves only the expected port, logs every probe),
# docker (compose/version/info pass, `docker port` returns nothing), and
# pins GPU_BACKEND=cpu so detection never touches real hardware.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/ods-preflight.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# --- fixture: script + safe-env lib live side by side, .env is the knob ------
FAKE_ODS="$TMP_ROOT/ods"
mkdir -p "$FAKE_ODS/lib"
cp "$SCRIPT" "$FAKE_ODS/ods-preflight.sh"
cp "$ODS_ROOT/lib/safe-env.sh" "$FAKE_ODS/lib/"

BIN="$TMP_ROOT/bin"
mkdir -p "$BIN"
CURL_LOG="$TMP_ROOT/curl.log"

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
url="${!#}"
port="$(printf '%s' "$url" | sed -n 's|.*://[^:/]*:\([0-9][0-9]*\)/.*|\1|p')"
echo "${port:-none} $url" >>"${CURL_LOG:?}"
[[ "$port" == "${SERVE_PORT:-}" ]] && exit 0
exit 1
EOF
cat >"$BIN/docker" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    --version) echo "Docker version 24.0.7, build test" ;;
    info) exit 0 ;;
    compose) echo "Docker Compose version v2.24.0" ;;
    port|ps|inspect) exit 1 ;;
    *) exit 1 ;;
esac
EOF
chmod +x "$BIN"/*

run_preflight() {  # $1=serve-port; extra env assignments follow
    local serve_port="$1"; shift
    : >"$CURL_LOG"
    env -i \
        PATH="$BIN:/usr/bin:/bin" \
        HOME="$TMP_ROOT/home" \
        CURL_LOG="$CURL_LOG" \
        SERVE_PORT="$serve_port" \
        TERM=dumb \
        "$@" \
        bash "$FAKE_ODS/ods-preflight.sh" || true
}

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== ods-preflight LLM port resolution ==="

# Case 1: .env without OLLAMA_PORT — the fallback must be the canonical
# host default 11434, not the container-internal 8080.
printf 'GPU_BACKEND=cpu\n' >"$FAKE_ODS/.env"
run_preflight 11434 >"$TMP_ROOT/out1.log" 2>&1
grep -q 'LLM endpoint (llama-server) responding at' "$TMP_ROOT/out1.log" \
    || fail "llama-server check did not pass (out: $(grep -i llm "$TMP_ROOT/out1.log" | head -3))"
grep -q '^11434 ' "$CURL_LOG" \
    || fail "port 11434 was never probed (probed: $(cat "$CURL_LOG"))"
grep -q '^8080 ' "$CURL_LOG" \
    && fail "container-internal 8080 was probed as a host port"
echo "PASS: absent OLLAMA_PORT falls back to 11434, never 8080"

# Case 2: explicit OLLAMA_PORT in .env still wins (guard against the fix
# breaking the configured path).
printf 'GPU_BACKEND=cpu\nOLLAMA_PORT=12345\n' >"$FAKE_ODS/.env"
run_preflight 12345 >"$TMP_ROOT/out2.log" 2>&1
grep -q 'LLM endpoint (llama-server) responding at' "$TMP_ROOT/out2.log" \
    || fail "configured-port run did not pass"
grep -q '^12345 ' "$CURL_LOG" \
    || fail "configured OLLAMA_PORT=12345 was never probed"
echo "PASS: configured OLLAMA_PORT is honored"

# Case 3: deprecated LLAMA_SERVER_PORT alias still works when OLLAMA_PORT
# is absent (back-compat guard).
printf 'GPU_BACKEND=cpu\nLLAMA_SERVER_PORT=11444\n' >"$FAKE_ODS/.env"
run_preflight 11444 >"$TMP_ROOT/out3.log" 2>&1
grep -q 'LLM endpoint (llama-server) responding at' "$TMP_ROOT/out3.log" \
    || fail "alias run did not pass"
grep -q '^11444 ' "$CURL_LOG" \
    || fail "LLAMA_SERVER_PORT=11444 alias was not honored"
echo "PASS: deprecated LLAMA_SERVER_PORT alias still resolves"

echo "PASS: all ods-preflight LLM port cases"
