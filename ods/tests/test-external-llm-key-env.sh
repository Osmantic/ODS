#!/usr/bin/env bash
# --external-llm-key-env VAR takes an API-mode key from the environment, as
# Windows setup passes it (WSLENV), so the key never appears in argv. The
# log directory is missing on purpose: install-core stops right after its
# option checks, so nothing is installed or changed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
key="sk-fixture-$(printf 'k%.0s' {1..24})"

run_core() {
    env -u ODS_GATEWAY_ONLY INSTALL_DIR="$fixture/install" LOG_FILE="$fixture/missing/log" \
        "$@" "$ROOT/install-core.sh" --pixel --non-interactive --skip-docker \
        --external-llm-url https://api.example.test --external-llm-provider openai-compatible \
        --external-llm-model fixture-model --external-llm-key-env ODS_TEST_EXTERNAL_KEY \
        >"$fixture/output" 2>&1 || true
}

run_core ODS_TEST_EXTERNAL_KEY="$key"
grep -q 'must name a variable holding one API key' "$fixture/output" \
    && fail "a valid key from the environment was refused: $(cat "$fixture/output")"
grep -q 'Installer log directory is missing or unsafe' "$fixture/output" \
    || fail "install-core did not get past its option checks: $(cat "$fixture/output")"
grep -qF "$key" "$fixture/output" && fail "the key was echoed"
[[ ! -e "$fixture/install" ]] || fail "the option check changed installation state"

for bad in "" "two words" "line
break"; do
    run_core ODS_TEST_EXTERNAL_KEY="$bad"
    grep -q 'must name a variable holding one API key' "$fixture/output" \
        || fail "an invalid key '$bad' was accepted: $(cat "$fixture/output")"
done
run_core
grep -q 'must name a variable holding one API key' "$fixture/output" \
    || fail "an unset key variable was accepted"

if env INSTALL_DIR="$fixture/install" LOG_FILE="$fixture/missing/log" "$ROOT/install-core.sh" \
    --external-llm-key-env 'not a name' >"$fixture/output" 2>&1; then
    fail "an invalid variable name was accepted"
fi
grep -q 'requires an environment variable name' "$fixture/output" || fail "the name error was unclear"

echo 'PASS: --external-llm-key-env takes the key from the environment, validates it, and never echoes it'
