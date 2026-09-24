#!/usr/bin/env bash
# ============================================================================
# Regression: auth-env.sh resolves the host-side LLM probe port correctly.
#
# llama-server publishes ${OLLAMA_PORT:-11434}:8080 — 8080 is the
# container-internal port nothing host-side listens on. Before this fix,
# test-integration.sh probed localhost:${OLLAMA_PORT:-8080} and auth-env.sh
# never loaded OLLAMA_PORT, so the llama-server checks hit a dead port on
# every standard install (and only passed by accident on native Lemonade
# installs where 8080 is genuinely the host port).
#
# Contract:
#   AMD_INFERENCE_PORT (native Lemonade) wins when set
#   then OLLAMA_PORT, then deprecated LLAMA_SERVER_PORT, then 11434
#   shell env always beats .env
# ============================================================================
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
AUTH_ENV="${ODS_ROOT}/tests/lib/auth-env.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASSED=0
FAILED=0

# run_load <env-file-content> [extra env assignments...] -> prints LLM_PORT
run_load() {
    local env_content="$1"; shift
    local home="$TMP_ROOT/home$RANDOM$RANDOM"
    mkdir -p "$home"
    printf '%s\n' "$env_content" > "$home/.env"
    # shellcheck disable=SC2016 # single-quoted on purpose: expands in the child
    env -i PATH="/usr/bin:/bin" ODS_HOME="$home" "$@" \
        bash -c 'source "$1"; _ae_load; printf "%s" "$LLM_PORT"' _ "$AUTH_ENV"
}

check() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "PASS $name"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL $name: expected $expected, got $actual"
        FAILED=$((FAILED + 1))
    fi
}

check "no LLM keys -> 11434 external default" 11434 \
    "$(run_load 'DASHBOARD_API_PORT=3002')"

check "OLLAMA_PORT from .env" 12345 \
    "$(run_load 'OLLAMA_PORT=12345')"

check "deprecated LLAMA_SERVER_PORT alias" 11435 \
    "$(run_load 'LLAMA_SERVER_PORT=11435')"

check "AMD_INFERENCE_PORT wins (native Lemonade)" 8080 \
    "$(run_load 'OLLAMA_PORT=11434
AMD_INFERENCE_PORT=8080')"

check "empty AMD_INFERENCE_PORT falls through to OLLAMA_PORT" 11434 \
    "$(run_load 'AMD_INFERENCE_PORT=
OLLAMA_PORT=11434')"

check "shell env beats .env" 23456 \
    "$(run_load 'OLLAMA_PORT=12345' OLLAMA_PORT=23456)"

# shellcheck disable=SC2016
check "missing .env -> 11434" 11434 \
    "$(env -i PATH="/usr/bin:/bin" ODS_HOME="$TMP_ROOT/no-such-home" \
        bash -c 'source "$1"; _ae_load; printf "%s" "$LLM_PORT"' _ "$AUTH_ENV")"

# Contract: the host-side call sites fixed here may not regress to the
# container-internal port. (macOS native legitimately uses 8080; other
# known host-side stragglers are tracked by their own changes.)
if grep -rn 'OLLAMA_PORT:-8080\|LLM_PORT:-8080' \
        "$ODS_ROOT/installers/phases" \
        "$ODS_ROOT/tests/test-integration.sh" "$ODS_ROOT/tests/lib" \
        >/dev/null 2>&1; then
    echo "FAIL container-internal 8080 used as a host-side llama-server fallback"
    grep -rn 'OLLAMA_PORT:-8080\|LLM_PORT:-8080' \
        "$ODS_ROOT/installers/phases" \
        "$ODS_ROOT/tests/test-integration.sh" "$ODS_ROOT/tests/lib" || true
    FAILED=$((FAILED + 1))
else
    echo "PASS no container-internal 8080 host-side fallbacks"
    PASSED=$((PASSED + 1))
fi

echo ""
echo "Passed: $PASSED  Failed: $FAILED"
[[ "$FAILED" -eq 0 ]]
