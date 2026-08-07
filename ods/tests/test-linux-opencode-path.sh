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

grep -q '__HOME__' "$SERVICE" \
  && pass "systemd service templates home directory" \
  || fail "systemd service must template home directory"

if grep -q 'ExecStart=__HOME__/.opencode/bin/opencode' "$SERVICE"; then
  fail "systemd service still hard-codes ~/.opencode/bin/opencode"
fi

echo "=== sed escape regression guard ==="

_slashed=$(printf '%s\n' '/home/patil' | sed 's/[&\\]/\\&/g')
[ "$_slashed" = '/home/patil' ] \
  && pass "sed escape leaves forward slashes intact" \
  || fail "sed escape must not rewrite / into &"

echo "=== placeholder rendering smoke ==="

_opencode_bin_esc=$(printf '%s\n' '/home/patil/.opencode/bin/opencode' | sed 's/[&\\]/\\&/g')
_opencode_bin_dir_esc=$(printf '%s\n' '/home/patil/.opencode/bin' | sed 's/[&\\]/\\&/g')
_rendered=$(sed -e "s|__HOME__|${_slashed}|g" \
  -e "s|__OPENCODE_BIN__|${_opencode_bin_esc}|g" \
  -e "s|__OPENCODE_BIN_DIR__|${_opencode_bin_dir_esc}|g" "$SERVICE")

printf '%s\n' "$_rendered" | grep -q '__HOME__' \
  && fail "rendered unit still contains __HOME__ placeholder" \
  || pass "rendered unit substitutes __HOME__ placeholder"

printf '%s\n' "$_rendered" | grep -q '__OPENCODE_BIN__' \
  && fail "rendered unit still contains __OPENCODE_BIN__ placeholder" \
  || pass "rendered unit substitutes __OPENCODE_BIN__ placeholder"

printf '%s\n' "$_rendered" | grep -q '__OPENCODE_BIN_DIR__' \
  && fail "rendered unit still contains __OPENCODE_BIN_DIR__ placeholder" \
  || pass "rendered unit substitutes __OPENCODE_BIN_DIR__ placeholder"

_workdir_line=$(printf '%s\n' "$_rendered" | grep '^WorkingDirectory=')
[ "$_workdir_line" = 'WorkingDirectory=/home/patil' ] \
  && pass "rendered WorkingDirectory is a clean slash path" \
  || fail "rendered WorkingDirectory corrupted: $_workdir_line"

printf '%s\n' "$_workdir_line" | grep -q '&' \
  && fail "rendered WorkingDirectory contains a stray &" \
  || pass "rendered WorkingDirectory has no stray &"

echo "[PASS] Linux OpenCode path tests"
