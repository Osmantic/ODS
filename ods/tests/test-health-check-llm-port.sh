#!/usr/bin/env bash
# health-check.sh must run its inference probe against llama-server's
# published host port (OLLAMA_PORT, default 11434), not the container port
# 8080 that nothing listens on from the host.
#
# Run from repo root:  bash ods/tests/test-health-check-llm-port.sh
# Or from ods:         bash tests/test-health-check-llm-port.sh
set -uo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        # The probe must expand in the candidate shell, not this one.
        # shellcheck disable=SC2016
        if [ -x "$_modern_bash" ] && [ "$("$_modern_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] health-check.sh requires Bash 4+ (service registry); this host only has Bash ${BASH_VERSION}"
    echo "Result: 0 passed, 0 failed, 1 skipped"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HC="$ROOT_DIR/scripts/health-check.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

[[ -f "$HC" ]] || { echo "health-check.sh not found"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/install" "$TMP/bin"
# Docker daemon unreachable: container checks fail cleanly and fast.
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$TMP/bin/docker"
# curl stub: record every probed URL and answer only the inference request.
cat > "$TMP/bin/curl" <<'STUB'
#!/bin/sh
url=""
for arg in "$@"; do
    case "$arg" in http://*) url="$arg" ;; esac
done
echo "$url" >> "$CURL_LOG"
case "$url" in
    */v1/completions) echo '{"choices":[{"text":"Hi"}]}' ;;
    *) exit 7 ;;
esac
STUB
chmod +x "$TMP/bin/docker" "$TMP/bin/curl"

run_hc() {
    : > "$TMP/curl.log"
    env -u LLM_PORT -u OLLAMA_PORT CURL_LOG="$TMP/curl.log" INSTALL_DIR="$TMP/install" \
        TIMEOUT=1 PATH="$TMP/bin:$PATH" "$BASH" "$HC" --json > "$TMP/out.json" 2> "$TMP/err.txt"
}

llm_url() { grep '/completions$' "$TMP/curl.log" | head -n 1; }

echo "Test 1: probes the OLLAMA_PORT host port from .env"
printf 'OLLAMA_PORT=43123\n' > "$TMP/install/.env"
run_hc
if [[ "$(llm_url)" == "http://localhost:43123/v1/completions" ]]; then
    pass "inference probe uses OLLAMA_PORT"
else
    fail "inference probe hit '$(llm_url)', expected port 43123"
fi
if grep -q '"llm": "ok"' "$TMP/out.json"; then
    pass "llm reported ok when the published port answers"
else
    fail "llm not ok: $(grep '"llm"' "$TMP/out.json")"
fi

echo "Test 2: defaults to the manifest host port 11434 without OLLAMA_PORT"
: > "$TMP/install/.env"
run_hc
if [[ "$(llm_url)" == "http://localhost:11434/v1/completions" ]]; then
    pass "inference probe defaults to 11434"
else
    fail "inference probe hit '$(llm_url)', expected port 11434"
fi

echo "Test 3: an explicit LLM_PORT still wins"
printf 'OLLAMA_PORT=43123\n' > "$TMP/install/.env"
: > "$TMP/curl.log"
LLM_PORT=45555 CURL_LOG="$TMP/curl.log" INSTALL_DIR="$TMP/install" TIMEOUT=1 \
    PATH="$TMP/bin:$PATH" "$BASH" "$HC" --json > "$TMP/out.json" 2> "$TMP/err.txt"
if [[ "$(llm_url)" == "http://localhost:45555/v1/completions" ]]; then
    pass "LLM_PORT override honored"
else
    fail "inference probe hit '$(llm_url)', expected port 45555"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
