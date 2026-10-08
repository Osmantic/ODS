#!/usr/bin/env bash
# Phase 12's in-network API probe names ODS as its User-Agent (some API front
# ends refuse the default Python one, Cloudflare error 1010) and turns HTTP
# errors into hints. Docker is a stub here, so nothing is contacted.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

function_text="$(awk '/^_phase12_verify_external_llm_completion\(\) \{/ { emit = 1 }
    emit { print }
    emit && /^}$/ { exit }' "$ROOT/installers/phases/12-health.sh")"
[[ -n "$function_text" ]] || fail "could not find the probe function in 12-health.sh"
eval "$function_text"

ai() { printf '%s\n' "$*"; }
ai_bad() { printf '%s\n' "$*"; }
sr_container() { echo ods-dashboard-api; }
_phase12_env_get() { printf '%s\n' "$2"; }
external_llm_resolve_model() { return 0; }
external_llm_probe_completion() { return 0; }
external_llm_read_api_key() { printf 'fixture-key-value'; }

{
    echo '#!/usr/bin/env bash'
    echo 'cat >/dev/null'
    echo 'printf "%s\n" "$@" >"$STUB_ARGS"'
    echo 'printf "%s\n" "$STUB_REPLY"'
    echo 'exit "$STUB_STATUS"'
} >"$fixture/docker"
chmod +x "$fixture/docker"

export STUB_ARGS="$fixture/args"
DOCKER_CMD="$fixture/docker"
EXTERNAL_LLM_URL=https://api.example.test
EXTERNAL_LLM_CONTAINER_URL=https://api.example.test
EXTERNAL_LLM_PROVIDER=openai-compatible
EXTERNAL_LLM_MODEL=fixture-model
EXTERNAL_LLM_API_KEY_FILE="$fixture/key"
LOG_FILE="$fixture/install.log"
VERSION=9.9.9
BGRN=""
NC=""

probe() {
    export STUB_REPLY="$1" STUB_STATUS="$2"
    : >"$LOG_FILE"
    if output="$(_phase12_verify_external_llm_completion 2>&1)"; then status=0; else status=$?; fi
}

probe "assistant token received" 0
[[ "$status" -eq 0 ]] || fail "a passing probe failed: $output"
[[ "$(tail -n 1 "$STUB_ARGS")" == "ODS/9.9.9" ]] || fail "the probe was not given the ODS User-Agent"
grep -q 'fixture-key-value' "$STUB_ARGS" && fail "the API key reached the docker command line"

probe "API answered HTTP 403: error code: 1010" 1
[[ "$status" -ne 0 ]] || fail "a refused probe passed"
grep -q 'refused this request from the ODS Docker network (HTTP 403)' <<<"$output" \
    || fail "HTTP 403 gave no refusal hint: $output"
grep -q 'error code: 1010' "$LOG_FILE" || fail "the API reply was not saved to the log"

# A LiteLLM proxy's refusal echoes the key's end and its hash; the log keeps
# neither (fleet, DSV4.1 drill proxy).
hash="$(printf '0123456789abcdef%.0s' 1 2 3 4)"
probe "API answered HTTP 401: {\"error\":{\"message\":\"Authentication Error, Invalid proxy server token passed. Received API Key = sk-...wxyz, Key Hash (Token) = $hash. Unable to find token in cache\"}}" 1
grep -q 'wxyz' "$LOG_FILE" && fail "the log kept the end of the refused key: $(cat "$LOG_FILE")"
grep -q "$hash" "$LOG_FILE" && fail "the log kept the refused key's hash"
grep -qF 'Received API Key = [redacted], Key Hash (Token) = [redacted]. Unable to find token' "$LOG_FILE" \
    || fail "the redacted reply was not saved to the log: $(cat "$LOG_FILE")"

probe "API answered HTTP 429: slow down" 1
grep -q 'rate-limiting this key (HTTP 429)' <<<"$output" || fail "HTTP 429 gave no rate-limit hint: $output"

probe "API answered HTTP 502: bad gateway" 1
grep -q "The API answered HTTP 502. Its reply is saved in $LOG_FILE" <<<"$output" \
    || fail "another HTTP error gave no status: $output"

probe "urlopen error [Errno -2] Name or service not known" 1
grep -q 'Check the saved probe error' <<<"$output" || fail "a transport error lost the generic hint: $output"

echo 'PASS: the in-network API probe sends the ODS User-Agent and explains HTTP errors'
