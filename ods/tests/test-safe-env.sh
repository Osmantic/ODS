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

echo "Test 12: load_env_file only strips a matching pair of surrounding quotes"
unset DQ_INNER_SINGLE SQ_INNER_DOUBLE DQ_ONLY_SINGLE PLAIN_DQ PLAIN_SQ 2>/dev/null || true
# A double-quoted value whose content is itself single-quoted must keep the
# inner single quotes; the previous independent per-quote stripping dropped them.
cat > "$tmpdir/.env-quotes" << 'EOF'
DQ_INNER_SINGLE="'literal'"
SQ_INNER_DOUBLE='"json"'
DQ_ONLY_SINGLE="'"
PLAIN_DQ="plain"
PLAIN_SQ='plain'
EOF
load_env_file "$tmpdir/.env-quotes"
[[ "${DQ_INNER_SINGLE:-}" == "'literal'" ]] || fail "DQ_INNER_SINGLE lost inner single quotes (got: '${DQ_INNER_SINGLE:-}')"
[[ "${SQ_INNER_DOUBLE:-}" == '"json"' ]] || fail "SQ_INNER_DOUBLE lost inner double quotes (got: '${SQ_INNER_DOUBLE:-}')"
[[ "${DQ_ONLY_SINGLE:-}" == "'" ]] || fail "DQ_ONLY_SINGLE collapsed (got: '${DQ_ONLY_SINGLE:-}')"
[[ "${PLAIN_DQ:-}" == "plain" ]] || fail "PLAIN_DQ not unquoted (got: '${PLAIN_DQ:-}')"
[[ "${PLAIN_SQ:-}" == "plain" ]] || fail "PLAIN_SQ not unquoted (got: '${PLAIN_SQ:-}')"
pass "load_env_file strips only matched surrounding quote pairs"

echo "Test 13: load_env_file decodes the supported double-quoted escape set"
unset FILE_ESCAPED 2>/dev/null || true
cat > "$tmpdir/.env-escaped" << 'EOF'
FILE_ESCAPED="it's \$HOME and \$(whoami) and \`id\` and C:\\path and \"dq\""
EOF
load_env_file "$tmpdir/.env-escaped"
[[ "${FILE_ESCAPED:-}" == 'it'"'"'s $HOME and $(whoami) and `id` and C:\path and "dq"' ]] \
    || fail "FILE_ESCAPED not decoded safely (got: ${FILE_ESCAPED:-})"
pass "load_env_file decodes supported escapes without evaluation"

echo "Test 14: load_env_file applies Compose's inline-comment and whitespace rules"
# ods-cli exports these values before calling docker compose, and Compose gives
# the environment precedence over .env — so a value Compose would read as
# "11434" must not reach it as "11434 # comment" (invalid hostPort).
unset IC_PORT IC_HASH_NO_SPACE IC_DQ IC_SQ IC_DQ_INNER IC_SQ_INNER IC_PAD IC_EMPTY_COMMENT IC_LEADING_HASH IC_DQ_PAD 2>/dev/null || true
cat > "$tmpdir/.env-comments" << 'EOF'
IC_PORT=11434          # llama-server API (external → internal 8080)
IC_HASH_NO_SPACE=abc#not-a-comment
IC_DQ="quoted value" # trailing note
IC_SQ='single value'   # trailing note
IC_DQ_INNER="keep # this"
IC_SQ_INNER='keep # this too'
IC_EMPTY_COMMENT=  # auto-generated during install
IC_LEADING_HASH= #x
EOF
# Trailing whitespace on purpose; written with printf so the source stays clean.
printf '%s\n' 'IC_PAD=   padded value   ' 'IC_DQ_PAD=  "spaced quotes"  ' >> "$tmpdir/.env-comments"
load_env_file "$tmpdir/.env-comments"
[[ "${IC_PORT:-}" == "11434" ]] || fail "IC_PORT kept its inline comment (got: '${IC_PORT:-}')"
[[ "${IC_HASH_NO_SPACE:-}" == "abc#not-a-comment" ]] || fail "IC_HASH_NO_SPACE lost a '#' that is not a comment (got: '${IC_HASH_NO_SPACE:-}')"
[[ "${IC_DQ:-}" == "quoted value" ]] || fail "IC_DQ kept the comment after its closing quote (got: '${IC_DQ:-}')"
[[ "${IC_SQ:-}" == "single value" ]] || fail "IC_SQ kept the comment after its closing quote (got: '${IC_SQ:-}')"
[[ "${IC_DQ_INNER:-}" == "keep # this" ]] || fail "IC_DQ_INNER lost quoted content (got: '${IC_DQ_INNER:-}')"
[[ "${IC_SQ_INNER:-}" == "keep # this too" ]] || fail "IC_SQ_INNER lost quoted content (got: '${IC_SQ_INNER:-}')"
[[ "${IC_PAD:-}" == "padded value" ]] || fail "IC_PAD kept surrounding whitespace (got: '${IC_PAD:-}')"
# Compose trims leading whitespace BEFORE the inline-comment cut, so
# "KEY=  # x" becomes "# x" — the '#' is no longer preceded by a space and is
# kept as a literal value (verified with `docker compose config`). safe-env
# must match, or ods-cli would export a value Compose never produced.
[[ "${IC_EMPTY_COMMENT-unset}" == "# auto-generated during install" ]] || fail "IC_EMPTY_COMMENT should be the literal comment like Compose (got: '${IC_EMPTY_COMMENT-unset}')"
[[ "${IC_LEADING_HASH-unset}" == "#x" ]] || fail "IC_LEADING_HASH should stay '#x' like Compose (got: '${IC_LEADING_HASH-unset}')"
[[ "${IC_DQ_PAD:-}" == "spaced quotes" ]] || fail "IC_DQ_PAD not unquoted after trimming (got: '${IC_DQ_PAD:-}')"
pass "load_env_file matches Compose for inline comments and surrounding whitespace"

echo ""
echo "All safe-env tests passed."
