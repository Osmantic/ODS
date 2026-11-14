#!/usr/bin/env bash
# Compact tests for _ods_cli_wait_for_vllm_ready extracted from ods-cli.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_CLI="${ODS_CLI:-$SCRIPT_DIR/../ods-cli}"

if [ ! -f "$ODS_CLI" ]; then
  echo "FAIL: ods-cli not found at $ODS_CLI" >&2
  exit 1
fi

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

HELPER_SRC="$TMPDIR_TEST/helper.sh"
awk '
  /^_ods_cli_wait_for_vllm_ready[[:space:]]*\(\)/ { capture=1 }
  capture { print }
  capture && /^}/ { exit }
' "$ODS_CLI" > "$HELPER_SRC"

if [ ! -s "$HELPER_SRC" ]; then
  echo "FAIL: could not extract _ods_cli_wait_for_vllm_ready from $ODS_CLI" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$HELPER_SRC"

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# Stub sleep: records elapsed increments to a file (command substitution loses vars).
STUB_SLEEP="$TMPDIR_TEST/stub_sleep.sh"
cat > "$STUB_SLEEP" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >> "${_ODS_TEST_SLEEP_LOG:?}"
exit 0
EOF
chmod +x "$STUB_SLEEP"

# Health stub: reads a sequence of health values from a file, one per call.
# Writes the current health to a counter file so tests can inspect it.
HEALTH_STUB="$TMPDIR_TEST/health_stub.sh"
cat > "$HEALTH_STUB" <<'EOF'
#!/usr/bin/env bash
seq_file="${_ODS_TEST_HEALTH_SEQ:?}"
idx_file="${_ODS_TEST_HEALTH_IDX:?}"
idx=0
[ -f "$idx_file" ] && idx="$(cat "$idx_file")"
line="$(sed -n "$((idx + 1))p" "$seq_file")"
if [ -z "$line" ]; then
  line="$(tail -n 1 "$seq_file")"
fi
printf '%s' "$((idx + 1))" > "$idx_file"
case "$line" in
  __FAIL__) exit 1 ;;
  *) printf '%s\n' "$line"; exit 0 ;;
esac
EOF
chmod +x "$HEALTH_STUB"

# Log/warn stubs.
log() { printf 'LOG: %s\n' "$*" >> "${_ODS_TEST_LOG:?}"; }
warn() { printf 'WARN: %s\n' "$*" >> "${_ODS_TEST_LOG:?}"; }

setup_case() {
  local name="$1"
  shift
  CASE_DIR="$TMPDIR_TEST/$name"
  mkdir -p "$CASE_DIR"
  export _ODS_TEST_HEALTH_SEQ="$CASE_DIR/health_seq"
  export _ODS_TEST_HEALTH_IDX="$CASE_DIR/health_idx"
  export _ODS_TEST_SLEEP_LOG="$CASE_DIR/sleep_log"
  export _ODS_TEST_LOG="$CASE_DIR/log"
  : > "$_ODS_TEST_HEALTH_SEQ"
  : > "$_ODS_TEST_SLEEP_LOG"
  : > "$_ODS_TEST_LOG"
  for v in "$@"; do printf '%s\n' "$v" >> "$_ODS_TEST_HEALTH_SEQ"; done
  _ods_cli_vllm_health() { "$HEALTH_STUB"; }
  sleep() { "$STUB_SLEEP" "$@"; }
}

# 1. starting -> healthy
setup_case starting_then_healthy starting healthy
if _ods_cli_wait_for_vllm_ready 10 5; then
  if [ "$(cat "$_ODS_TEST_SLEEP_LOG")" = "5" ] && grep -q 'starting' "$_ODS_TEST_LOG"; then
    pass "starting -> healthy returns 0, logs initial message, sleeps once"
  else
    fail "starting -> healthy: unexpected sleep/log state"
  fi
else
  fail "starting -> healthy should return 0"
fi

# 2. immediate healthy
setup_case immediate_healthy healthy
if _ods_cli_wait_for_vllm_ready 10 5; then
  if [ ! -s "$_ODS_TEST_SLEEP_LOG" ]; then
    pass "immediate healthy returns 0 without sleeping"
  else
    fail "immediate healthy should not sleep"
  fi
else
  fail "immediate healthy should return 0"
fi

# 3. healthy immediate even with timeout 0
setup_case healthy_timeout_zero healthy
if _ods_cli_wait_for_vllm_ready 0 5; then
  pass "healthy with timeout 0 returns 0"
else
  fail "healthy with timeout 0 should return 0"
fi

# 4. starting with timeout 0 fails
setup_case starting_timeout_zero starting
if _ods_cli_wait_for_vllm_ready 0 5; then
  fail "starting with timeout 0 should fail"
else
  if grep -q 'Timed out' "$_ODS_TEST_LOG"; then
    pass "starting with timeout 0 fails with timeout warning"
  else
    fail "starting with timeout 0 should warn about timeout"
  fi
fi

# 5. unhealthy
setup_case unhealthy unhealthy
if _ods_cli_wait_for_vllm_ready 10 5; then
  fail "unhealthy should fail"
else
  if grep -q "ods status" "$_ODS_TEST_LOG" && grep -q "ods logs vllm-server" "$_ODS_TEST_LOG"; then
    pass "unhealthy fails with status/logs guidance"
  else
    fail "unhealthy should warn with status/logs guidance"
  fi
fi

# 6. stopped
setup_case stopped stopped
if _ods_cli_wait_for_vllm_ready 10 5; then
  fail "stopped should fail"
else
  if grep -q "ods status" "$_ODS_TEST_LOG"; then
    pass "stopped fails with status guidance"
  else
    fail "stopped should warn with status guidance"
  fi
fi

# 7. unknown
setup_case unknown weird_state
if _ods_cli_wait_for_vllm_ready 10 5; then
  fail "unknown state should fail"
else
  if grep -q "ods logs vllm-server" "$_ODS_TEST_LOG"; then
    pass "unknown state fails with logs guidance"
  else
    fail "unknown state should warn with logs guidance"
  fi
fi

# 8. health command nonzero refusal
setup_case health_nonzero __FAIL__
if _ods_cli_wait_for_vllm_ready 10 5; then
  fail "nonzero health should fail"
else
  if grep -q "Unable to determine" "$_ODS_TEST_LOG"; then
    pass "nonzero health refuses with warning"
  else
    fail "nonzero health should warn about inability to determine health"
  fi
fi

# 9. timeout while starting
setup_case timeout_starting starting starting starting starting
if _ods_cli_wait_for_vllm_ready 10 5; then
  fail "timeout while starting should fail"
else
  if grep -q 'Timed out' "$_ODS_TEST_LOG"; then
    pass "timeout while starting fails with timeout warning"
  else
    fail "timeout while starting should warn about timeout"
  fi
fi

# 10. invalid timeout (non-integer) fails without sleeping
setup_case invalid_timeout healthy
if _ods_cli_wait_for_vllm_ready abc 5; then
  fail "invalid timeout should fail"
else
  if [ ! -s "$_ODS_TEST_SLEEP_LOG" ] && grep -q 'Invalid timeout' "$_ODS_TEST_LOG"; then
    pass "invalid timeout fails without sleeping"
  else
    fail "invalid timeout should fail without sleeping"
  fi
fi

# 11. timeout exceeding max fails
setup_case timeout_too_large healthy
if _ods_cli_wait_for_vllm_ready 999999 5; then
  fail "oversized timeout should fail"
else
  if grep -q 'exceeds maximum' "$_ODS_TEST_LOG"; then
    pass "oversized timeout fails with maximum warning"
  else
    fail "oversized timeout should warn about maximum"
  fi
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
