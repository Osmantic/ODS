#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/07-devtools.sh"
SERVICE="$ROOT_DIR/opencode/opencode-web.service"

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; exit 1; }

echo "=== Linux OpenCode path tests ==="

grep -q 'type -P opencode' "$PHASE" \
  && pass "phase resolves executable OpenCode from PATH" \
  || fail "phase must resolve executable OpenCode from PATH without accepting shell functions"

grep -q '_opencode_candidate_is_file' "$PHASE" \
  && pass "phase validates OpenCode as an absolute executable file" \
  || fail "phase must validate OpenCode as an absolute executable file"

grep -q 'OPENCODE_BIN="\$(_find_opencode_bin || true)"' "$PHASE" \
  && pass "phase stores resolved OpenCode binary" \
  || fail "phase must store resolved OpenCode binary"

grep -q '\[\[ -n "\$OPENCODE_BIN" && -x "\$OPENCODE_BIN" \]\]' "$PHASE" \
  && pass "phase configures PATH-installed OpenCode" \
  || fail "phase must not require ~/.opencode/bin/opencode for configuration"

grep -q '__OPENCODE_BIN__' "$SERVICE" \
  && pass "systemd service templates resolved binary" \
  || fail "systemd service must not hard-code ~/.opencode/bin/opencode"

grep -q '__OPENCODE_BIN_DIR__' "$SERVICE" \
  && pass "systemd service templates resolved binary directory" \
  || fail "systemd service PATH must include resolved binary directory"

if grep -q 'ExecStart=__HOME__/.opencode/bin/opencode' "$SERVICE"; then
  fail "systemd service still hard-codes ~/.opencode/bin/opencode"
fi

echo "[PASS] Linux OpenCode path tests"
