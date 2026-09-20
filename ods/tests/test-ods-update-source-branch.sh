#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }

grep -q 'git symbolic-ref --quiet --short HEAD' "$UPDATE_SCRIPT" || fail "no named branch guard"
grep -q 'source_branch=' "$UPDATE_SCRIPT" || fail "no checked-out branch capture"
grep -q 'git pull origin "\$source_branch"' "$UPDATE_SCRIPT" || fail "no branch-specific pull"
! grep -q 'git pull origin main' "$UPDATE_SCRIPT" || fail "main remains hard-coded"
! grep -q 'git pull origin master' "$UPDATE_SCRIPT" || fail "master remains hard-coded"

pass "ods-update follows the checked-out source branch"
