#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/installers/macos/ods-macos.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }

start_block="$(sed -n '/^cmd_start() {/,/^cmd_stop() {/p' "$SCRIPT")"
restart_block="$(sed -n '/^cmd_restart() {/,/^cmd_status() {/p' "$SCRIPT")"

grep -q 'Failed to start .*Docker Compose' <<<"$start_block" \
  || fail "service/all start does not report compose failure"
grep -q 'Failed to restart .*Docker Compose' <<<"$restart_block" \
  || fail "service/all restart does not report compose failure"
grep -q 'return 1' <<<"$start_block" || fail "start does not propagate failure"
grep -q 'return 1' <<<"$restart_block" || fail "restart does not propagate failure"

pass "macOS start and restart propagate Docker Compose failures"
