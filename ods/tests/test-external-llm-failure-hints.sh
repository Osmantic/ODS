#!/usr/bin/env bash
# When an API selected with --external-llm-url fails discovery, phase 02b
# says why: unreachable, key missing, key refused, rate-limited, another HTTP
# status, or a model the server does not list. curl is a stub that answers
# with a chosen HTTP status, so nothing is contacted.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# shellcheck source=../installers/lib/external-services.sh
source "$ROOT/installers/lib/external-services.sh"

ods_progress() { :; }
log() { :; }
ai() { printf '%s\n' "$*"; }
ai_ok() { printf '%s\n' "$*"; }
ai_bad() { printf '%s\n' "$*"; }
resolve_compose_config() { :; }

# Real curl -f exits 22 on an HTTP error; -w '%{http_code}' prints the status.
curl() {
    local arg write_status=false
    for arg in "$@"; do
        [[ "$arg" != '%{http_code}' ]] || write_status=true
    done
    [[ "$MOCK_STATUS" != unreachable ]] || return 7
    if $write_status; then
        printf '%s' "$MOCK_STATUS"
        return 0
    fi
    [[ "$MOCK_STATUS" == 200 ]] || return 22
    printf '{"data":[{"id":"model-a"},{"id":"model-b"}]}'
}

# usage: run_case STATUS PROVIDER MODEL [KEY]
run_case() {
    (
        MOCK_STATUS="$1"
        INSTALL_DIR="$fixture/install"
        ODS_MODE=local INTERACTIVE=false DRY_RUN=false
        GGUF_FILE="Fixture-9B-Q4_K_M.gguf" LLM_MODEL="fixture-9b"
        EXTERNAL_LLM_URL="https://api.example.test"
        EXTERNAL_LLM_PROVIDER="$2"
        EXTERNAL_LLM_MODEL="$3"
        EXTERNAL_LLM_API_KEY_VALUE="${4:-}"
        unset EXTERNAL_LLM_API_KEY_FILE EXTERNAL_LLM_DISABLE NATIVE_LLM_BASE_URL
        # shellcheck source=../installers/phases/02b-external-services.sh
        source "$ROOT/installers/phases/02b-external-services.sh" || exit 1
        printf 'selected=%s\n' "$EXTERNAL_LLM_MODEL"
    ) 2>&1
}

expect_stop() {
    local message="$1"; shift
    if output="$(run_case "$@")"; then
        fail "status $1 did not stop phase 02b: $output"
    fi
    grep -qF -- "$message" <<<"$output" || fail "status $1 did not say '$message': $output"
    grep -q 'does not expose the required model' <<<"$output" \
        && fail "status $1 still blamed the model: $output"
    return 0
}

mkdir -p "$fixture/install"

output="$(run_case 200 openai-compatible model-b)" || fail "a served model was refused: $output"
grep -q '^selected=model-b$' <<<"$output" || fail "the served model was not selected: $output"

expect_stop "Could not reach https://api.example.test." unreachable openai-compatible model-a
expect_stop "Could not reach https://api.example.test." unreachable auto model-a
expect_stop "https://api.example.test needs an API key." 401 openai-compatible model-a
expect_stop "https://api.example.test refused the API key." 403 openai-compatible model-a fixture-key
# The key hint names the flag of the installer the owner ran (fleet row 30:
# Windows owners were told to chmod).
output="$(run_case 401 openai-compatible model-a)" || true
grep -qF "(chmod 600), then rerun with --external-llm-key-file FILE." <<<"$output" \
    || fail "the Linux key hint changed: $output"
grep -qF "ExternalLlmKeyFile" <<<"$output" && fail "a Linux run was told the Windows flag: $output"
output="$(ODS_WINDOWS_SYSTEM_DIRECTORY='C:\Windows\system32' run_case 401 openai-compatible model-a)" || true
grep -qF "in a text file only you can read, then rerun with -ExternalLlmKeyFile FILE." <<<"$output" \
    || fail "a Windows run did not get the Windows key hint: $output"
grep -qF "chmod" <<<"$output" && fail "a Windows run was told to chmod: $output"
expect_stop "is rate-limiting requests (HTTP 429)" 429 openai-compatible model-a
expect_stop "answered HTTP 404 instead of a model list" 404 openai-compatible model-a
expect_stop "does not serve the model missing-model" 200 openai-compatible missing-model
grep -q 'Models it lists include: model-a, model-b' <<<"$output" \
    || fail "the served models were not listed: $output"
grep -q 'could not identify' <<<"${output,,}" && fail "an unreachable API was reported as an unknown provider"

echo 'PASS: phase 02b explains why a selected API failed discovery'
