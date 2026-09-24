#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: scripts/health-check.sh must probe the llama-server host
# port the install actually configured — OLLAMA_PORT (manifest default
# 11434) or AMD_INFERENCE_PORT for Lemonade — not the container-internal
# 8080. With the old hardcoded 8080 the critical inference check failed on
# every standard install and the tool always exited 2 ("critical").
#
# The fixture uses the real service registry + manifests and a stub curl
# that only serves the port it is told to expect, recording every port the
# script probes.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/scripts/health-check.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# --- fixture tree: real libs + every real manifest ---------------------------
FAKE_ODS="$TMP_ROOT/ods"
mkdir -p "$FAKE_ODS/lib" "$FAKE_ODS/extensions/services" "$FAKE_ODS/scripts"
cp "$ODS_ROOT/lib/safe-env.sh" "$ODS_ROOT/lib/service-registry.sh" \
    "$ODS_ROOT/lib/python-cmd.sh" "$FAKE_ODS/lib/"
cp "$SCRIPT" "$FAKE_ODS/scripts/health-check.sh"
for manifest in "$ODS_ROOT"/extensions/services/*/manifest.yaml; do
    sid="$(basename "$(dirname "$manifest")")"
    mkdir -p "$FAKE_ODS/extensions/services/$sid"
    cp "$manifest" "$FAKE_ODS/extensions/services/$sid/manifest.yaml"
done

INSTALL_DIR="$TMP_ROOT/install"
mkdir -p "$INSTALL_DIR" "$TMP_ROOT/home"

# --- stub curl: serve only $SERVE_PORT, log every probed port ----------------
BIN="$TMP_ROOT/bin"
mkdir -p "$BIN"
CURL_LOG="$TMP_ROOT/curl.log"

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
url="${!#}"
port="$(printf '%s' "$url" | sed -n 's|.*:\([0-9][0-9]*\)/.*|\1|p')"
echo "${port:-none} $url" >>"${CURL_LOG:?}"
if [[ "$port" == "${SERVE_PORT:-}" && "$url" == */completions ]]; then
    printf '{"text":"ok"}'
    exit 0
fi
exit 1
EOF
cat >"$BIN/docker" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$BIN"/*

run_check() {  # $1=serve-port; extra env assignments after
    local serve_port="$1"; shift
    : >"$CURL_LOG"
    env -i \
        PATH="$BIN:/usr/bin:/bin" \
        HOME="$TMP_ROOT/home" \
        INSTALL_DIR="$INSTALL_DIR" \
        CURL_LOG="$CURL_LOG" \
        SERVE_PORT="$serve_port" \
        "$@" \
        bash "$FAKE_ODS/scripts/health-check.sh" --json
}

fail() { echo "FAIL: $*" >&2; exit 1; }

# The script exits 2 whenever any core service is down, so the LLM check's
# own JSON result plus the stub curl's port log are the real discriminators.
assert_llm_ok() {  # $1=out-log $2=rc — a completed run is 1 (degraded) or 2 (critical)
    [[ "$2" =~ ^[12]$ ]] || fail "health-check crashed (exit $2)"
    grep -q '"llm": "ok"' "$1" || fail "llm check not ok: $(cat "$1")"
}

echo "=== health-check LLM port resolution ==="

# Case 1: default install — .env carries the stock OLLAMA_PORT=11434.
printf 'OLLAMA_PORT=11434\nLLM_API_BASE_PATH=/v1\n' >"$INSTALL_DIR/.env"
rc=0
run_check 11434 >"$TMP_ROOT/out1.log" 2>&1 || rc=$?
assert_llm_ok "$TMP_ROOT/out1.log" "$rc"
grep -q '^11434 .*/completions$' "$CURL_LOG" \
    || fail "LLM probe did not hit 11434 (probed: $(cat "$CURL_LOG"))"
echo "PASS: default install probes OLLAMA_PORT 11434"

# Case 2: custom OLLAMA_PORT in .env is honored.
printf 'OLLAMA_PORT=12345\nLLM_API_BASE_PATH=/v1\n' >"$INSTALL_DIR/.env"
rc=0
run_check 12345 >"$TMP_ROOT/out2.log" 2>&1 || rc=$?
assert_llm_ok "$TMP_ROOT/out2.log" "$rc"
grep -q '^12345 .*/completions$' "$CURL_LOG" \
    || fail "LLM probe did not hit custom OLLAMA_PORT=12345 (probed: $(cat "$CURL_LOG"))"
grep -q '^8080 ' "$CURL_LOG" \
    && fail "container-internal 8080 was probed as a host port"
echo "PASS: custom OLLAMA_PORT=12345 is probed"

# Case 3: Lemonade (AMD) install — AMD_INFERENCE_PORT + /api/v1.
printf 'AMD_INFERENCE_PORT=8080\nLLM_API_BASE_PATH=/api/v1\n' >"$INSTALL_DIR/.env"
rc=0
run_check 8080 >"$TMP_ROOT/out3.log" 2>&1 || rc=$?
assert_llm_ok "$TMP_ROOT/out3.log" "$rc"
grep -q '^8080 .*/api/v1/completions$' "$CURL_LOG" \
    || fail "Lemonade probe did not hit AMD_INFERENCE_PORT=8080 /api/v1"
echo "PASS: Lemonade AMD_INFERENCE_PORT=8080 still probed"

# Case 4: explicit LLM_PORT override always wins.
printf 'OLLAMA_PORT=11434\nLLM_API_BASE_PATH=/v1\n' >"$INSTALL_DIR/.env"
rc=0
run_check 9999 LLM_PORT=9999 >"$TMP_ROOT/out4.log" 2>&1 || rc=$?
assert_llm_ok "$TMP_ROOT/out4.log" "$rc"
grep -q '^9999 .*/completions$' "$CURL_LOG" \
    || fail "explicit LLM_PORT=9999 was not honored (probed: $(cat "$CURL_LOG"))"
echo "PASS: explicit LLM_PORT override wins"

echo "PASS: all health-check LLM port cases"
