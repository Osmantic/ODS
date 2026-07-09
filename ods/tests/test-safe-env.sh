#!/usr/bin/env bash
# Test lib/safe-env.sh: load_env_file and load_env_from_output
# Ensures .env loading is safe (no eval, no injection) and consistent.
#
# Run from repo root:  bash ods/tests/test-safe-env.sh
# Or from ods: bash tests/test-safe-env.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

# Source the implementation
[[ -f "$ROOT_DIR/lib/safe-env.sh" ]] || fail "lib/safe-env.sh not found"
. "$ROOT_DIR/lib/safe-env.sh"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# ---- load_env_file: valid keys and values ----
echo "Test 1: load_env_file parses valid KEY=value and exports"
cat > "$tmpdir/.env" << 'EOF'
# comment
SOME_KEY=simple_value
ANOTHER=with-dash_123
QUOTED_DOUBLE="value with spaces"
QUOTED_SINGLE='single quoted'
# empty above
EMPTY_VAL=
EOF
load_env_file "$tmpdir/.env"
[[ "${SOME_KEY:-}" == "simple_value" ]] || fail "SOME_KEY not set (got: ${SOME_KEY:-})"
[[ "${ANOTHER:-}" == "with-dash_123" ]] || fail "ANOTHER not set"
[[ "${QUOTED_DOUBLE:-}" == "value with spaces" ]] || fail "QUOTED_DOUBLE not set"
[[ "${QUOTED_SINGLE:-}" == "single quoted" ]] || fail "QUOTED_SINGLE not set"
pass "load_env_file exports valid vars"

# ---- load_env_file: dangerous line must not be executed ----
echo "Test 2: load_env_file skips/invalidates dangerous key names (no eval)"
# Key with shell metacharacters should be skipped by our key regex
cat > "$tmpdir/.env2" << 'EOF'
SAFE_VAR=ok
EVIL_KEY$(echo injected)=value
NORMAL_AFTER=works
EOF
unset SAFE_VAR EVIL_KEY NORMAL_AFTER 2>/dev/null || true
load_env_file "$tmpdir/.env2"
[[ "${SAFE_VAR:-}" == "ok" ]] || fail "SAFE_VAR not set"
[[ "${NORMAL_AFTER:-}" == "works" ]] || fail "NORMAL_AFTER not set"
# EVIL_KEY... should not be set (key regex rejects it)
pass "load_env_file rejects invalid key names"

# ---- load_env_file: missing file is no-op ----
echo "Test 3: load_env_file missing file is no-op"
load_env_file "$tmpdir/nonexistent.env"
pass "load_env_file missing file returns 0"

# ---- load_env_file: empty file ----
echo "Test 4: load_env_file empty file is no-op"
touch "$tmpdir/empty.env"
load_env_file "$tmpdir/empty.env"
pass "load_env_file empty file is no-op"

# ---- load_env_from_output: stdin (must run in current shell so export persists) ----
echo "Test 5: load_env_from_output parses KEY=\"value\" from stdin"
unset FROM_STDIN 2>/dev/null || true
load_env_from_output < <(echo 'FROM_STDIN="hello from stdin"')
[[ "${FROM_STDIN:-}" == "hello from stdin" ]] || fail "FROM_STDIN not set (got: ${FROM_STDIN:-})"
pass "load_env_from_output exports from stdin"

echo "Test 6: load_env_from_output tolerates CRLF script output"
unset FROM_CRLF 2>/dev/null || true
load_env_from_output < <(printf 'FROM_CRLF="windows python"\r\n')
[[ "${FROM_CRLF:-}" == "windows python" ]] || fail "FROM_CRLF not set from CRLF output"
pass "load_env_from_output strips trailing CR"

echo "Test 7: load_env_from_output parses escaped characters without eval"
unset ESCAPED DANGEROUS 2>/dev/null || true
load_env_from_output < <(printf '%s\n' 'ESCAPED="a \"quote\" and C:\\tmp and \$HOME and \`uname\`"')
[[ "${ESCAPED:-}" == 'a "quote" and C:\tmp and $HOME and `uname`' ]] || fail "ESCAPED not decoded safely (got: ${ESCAPED:-})"
_owned="$tmpdir/owned"
load_env_from_output < <(printf 'DANGEROUS="$(touch %s)"\n' "$_owned")
[[ "${DANGEROUS:-}" == "\$(touch $_owned)" ]] || fail "DANGEROUS value changed unexpectedly"
[[ ! -e "$_owned" ]] || fail "load_env_from_output executed command substitution"
pass "load_env_from_output does not evaluate values"

echo "Test 8: load_env_from_output_allowlist ignores unapproved keys"
unset ALLOWED NOT_ALLOWED 2>/dev/null || true
load_env_from_output_allowlist ALLOWED < <(printf '%s\n' 'ALLOWED="yes"' 'NOT_ALLOWED="no"')
[[ "${ALLOWED:-}" == "yes" ]] || fail "ALLOWED was not loaded"
[[ -z "${NOT_ALLOWED:-}" ]] || fail "NOT_ALLOWED should not be loaded"
pass "allowlist loader ignores unapproved keys"

echo "Test 9: model selector loader only accepts approved selector keys"
unset LLM_MODEL EVIL_SELECTOR_KEY 2>/dev/null || true
load_model_selector_env_from_output < <(printf '%s\n' 'LLM_MODEL="qwen-test"' 'EVIL_SELECTOR_KEY="nope"')
[[ "${LLM_MODEL:-}" == "qwen-test" ]] || fail "LLM_MODEL was not loaded"
[[ -z "${EVIL_SELECTOR_KEY:-}" ]] || fail "EVIL_SELECTOR_KEY should not be loaded"
pass "model selector loader is allowlisted"

echo "Test 10: load_env_file skips Bash readonly UID"
cat > "$tmpdir/.env-readonly" << 'EOF'
UID=12345
AFTER_READONLY_UID=still_loads
EOF
load_env_file "$tmpdir/.env-readonly"
[[ "${UID}" != "12345" ]] || fail "UID should not be overwritten"
[[ "${AFTER_READONLY_UID:-}" == "still_loads" ]] || fail "load_env_file stopped after readonly UID"
pass "load_env_file tolerates UID from .env"

echo "Test 11: load_env_file tolerates CRLF .env files (Windows/WSL2)"
unset CRLF_PORT CRLF_PATH CRLF_QUOTED 2>/dev/null || true
# Write a .env with Windows CRLF line endings. Without stripping the trailing
# CR, values keep it (8080\r) and the closing quote survives on quoted values.
printf 'CRLF_PORT=8080\r\nCRLF_PATH=/home/user/ods\r\nCRLF_QUOTED="abc123"\r\n' > "$tmpdir/.env-crlf"
load_env_file "$tmpdir/.env-crlf"
[[ "${CRLF_PORT:-}" == "8080" ]] || fail "CRLF_PORT has trailing CR (got len ${#CRLF_PORT}: '${CRLF_PORT:-}')"
[[ "${CRLF_PATH:-}" == "/home/user/ods" ]] || fail "CRLF_PATH has trailing CR (got: '${CRLF_PATH:-}')"
[[ "${CRLF_QUOTED:-}" == "abc123" ]] || fail "CRLF_QUOTED not unquoted/stripped (got: '${CRLF_QUOTED:-}')"
pass "load_env_file strips trailing CR from CRLF .env values"

echo ""
echo "All safe-env tests passed."
