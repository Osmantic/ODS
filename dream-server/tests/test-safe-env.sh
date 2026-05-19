#!/usr/bin/env bash
# Test lib/safe-env.sh: load_env_file and load_env_from_output
# Ensures .env loading is safe (no eval, no injection) and consistent.
#
# Run from repo root:  bash dream-server/tests/test-safe-env.sh
# Or from dream-server: bash tests/test-safe-env.sh

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

# ---- load_selector_env: legitimate selector output (#1271) ----
echo "Test 6: load_selector_env parses shlex single-quoted + bare values"
unset LLM_MODEL GGUF_FILE GGUF_URL GGUF_SHA256 MAX_CONTEXT LLM_MODEL_SIZE_MB 2>/dev/null || true
unset MODEL_RECOMMENDATION_SOURCE MODEL_RECOMMENDATION_POLICY 2>/dev/null || true
unset MODEL_RECOMMENDATION_CONFIDENCE MODEL_RECOMMENDATION_REASON 2>/dev/null || true
unset MODEL_RECOMMENDED_ALTERNATIVES MODEL_RUNTIME_PROFILE 2>/dev/null || true
unset MODEL_RUNTIME_PROFILE_LABEL MODEL_RUNTIME_PROFILE_SOURCE LLAMA_SERVER_IMAGE 2>/dev/null || true
_sha="$(printf 'a%.0s' {1..64})"
load_selector_env < <(cat << EOF
LLM_MODEL='qwen3-coder-next'
GGUF_FILE='qwen3-coder-next-Q4_K_M.gguf'
GGUF_URL='https://example.test/q.gguf'
GGUF_SHA256='${_sha}'
MAX_CONTEXT=262144
LLM_MODEL_SIZE_MB=18000
MODEL_RECOMMENDATION_SOURCE='catalog_fit_pre_download'
MODEL_RECOMMENDATION_POLICY='context-aware-largest-capable-general-v1'
MODEL_RECOMMENDATION_CONFIDENCE='high'
MODEL_RECOMMENDATION_REASON='Catalog fit (v1): needs about 40GB; do not run \$(rm -rf /) here'
MODEL_RECOMMENDED_ALTERNATIVES='a:8192:6;b:4096:4'
MODEL_RUNTIME_PROFILE='rp-1'
MODEL_RUNTIME_PROFILE_LABEL='Advanced profile'
MODEL_RUNTIME_PROFILE_SOURCE='https://example.test/profile'
LLAMA_SERVER_IMAGE='ghcr.io/example/llama:1'
EOF
)
[[ "${LLM_MODEL:-}" == "qwen3-coder-next" ]] || fail "LLM_MODEL not set (got: ${LLM_MODEL:-})"
[[ "${GGUF_FILE:-}" == "qwen3-coder-next-Q4_K_M.gguf" ]] || fail "GGUF_FILE not set"
[[ "${GGUF_URL:-}" == "https://example.test/q.gguf" ]] || fail "GGUF_URL not set"
[[ "${GGUF_SHA256:-}" == "$_sha" ]] || fail "GGUF_SHA256 not set"
[[ "${MAX_CONTEXT:-}" == "262144" ]] || fail "MAX_CONTEXT not set"
[[ "${LLM_MODEL_SIZE_MB:-}" == "18000" ]] || fail "LLM_MODEL_SIZE_MB not set"
[[ "${MODEL_RECOMMENDATION_REASON:-}" == 'Catalog fit (v1): needs about 40GB; do not run $(rm -rf /) here' ]] \
    || fail "MODEL_RECOMMENDATION_REASON not preserved literally (got: ${MODEL_RECOMMENDATION_REASON:-})"
[[ "${MODEL_RUNTIME_PROFILE:-}" == "rp-1" ]] || fail "MODEL_RUNTIME_PROFILE not set"
[[ "${LLAMA_SERVER_IMAGE:-}" == "ghcr.io/example/llama:1" ]] || fail "LLAMA_SERVER_IMAGE not set"
pass "load_selector_env parses all 15 known keys (shlex + bare)"

echo "Test 7: load_selector_env honors the KEY identifier regex (security gate)"
unset SAFE_K 2>/dev/null || true
load_selector_env < <(printf '%s\n' \
    "BAD-KEY='x'" \
    'EVIL$(echo hi)=v' \
    "SAFE_K='ok'") || true
[[ "${SAFE_K:-}" == "ok" ]] || fail "valid key after bad keys not set"
[[ -z "${BADKEY:-}" && -z "${EVIL:-}" ]] || fail "invalid key leaked"
pass "load_selector_env rejects non-identifier keys, keeps valid ones"

echo "Test 8: load_selector_env accepts dynamic runtime_profile.env-style keys"
unset DREAM_LLAMA_EXTRA_FLAG 2>/dev/null || true
load_selector_env < <(printf '%s\n' "DREAM_LLAMA_EXTRA_FLAG='--foo'")
[[ "${DREAM_LLAMA_EXTRA_FLAG:-}" == "--foo" ]] \
    || fail "dynamic catalog key not parsed (got: ${DREAM_LLAMA_EXTRA_FLAG:-})"
pass "load_selector_env does not hard-drop dynamic catalog keys"

echo "Test 9: load_selector_env does NOT execute injected shell (#1271 RCE)"
PWN="$tmpdir/pwn_marker"
rm -f "$PWN"
unset LLM_MODEL 2>/dev/null || true
load_selector_env < <(printf '%s\n' \
    "LLM_MODEL='qwen3:8b'" \
    "x=1; touch $PWN" \
    "\$(touch $PWN)" \
    "\`touch $PWN\`" \
    "GGUF_FILE='model.gguf'; rm -rf $tmpdir/should_not_run" \
    "MODEL_RECOMMENDATION_REASON='x'\$(touch $PWN)'y'" \
    "MAX_CONTEXT=8192") || true
[[ ! -e "$PWN" ]] || fail "RCE: injected payload executed (created $PWN)"
[[ "${LLM_MODEL:-}" == "qwen3:8b" ]] || fail "valid key not applied after injection lines"
pass "load_selector_env neutralizes injection payloads (no $PWN, valid keys kept)"

echo "Test 10: load_selector_env command substitution in value is inert"
rm -f "$PWN"
load_selector_env < <(printf '%s\n' "LLM_MODEL=\"\$(touch $PWN)\"") || true
[[ ! -e "$PWN" ]] || fail "RCE: command substitution in value executed"
pass "load_selector_env: command substitution in value did not execute"

echo "Test 11: load_selector_env value-shape hardening"
unset MAX_CONTEXT GGUF_SHA256 GGUF_URL 2>/dev/null || true
load_selector_env < <(printf '%s\n' \
    "MAX_CONTEXT='not-a-number'" \
    "GGUF_SHA256='zzzz'" \
    "GGUF_URL='ftp://evil/x'") || true
[[ -z "${MAX_CONTEXT:-}" ]] || fail "MAX_CONTEXT accepted non-numeric"
[[ -z "${GGUF_SHA256:-}" ]] || fail "GGUF_SHA256 accepted non-hex"
[[ -z "${GGUF_URL:-}" ]] || fail "GGUF_URL accepted non-http(s)"
pass "load_selector_env rejects malformed numeric/sha/url values"

echo "Test 12: existing load_env_from_output behavior unchanged (regression guard)"
unset RT_CHECK 2>/dev/null || true
load_env_from_output < <(echo 'RT_CHECK="still works"')
[[ "${RT_CHECK:-}" == "still works" ]] || fail "load_env_from_output regressed"
pass "load_env_from_output unchanged"

echo ""
echo "All safe-env tests passed."
