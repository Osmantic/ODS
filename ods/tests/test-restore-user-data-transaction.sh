#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESTORE="$ROOT_DIR/ods-restore.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }

[[ -x "$RESTORE" ]] || fail "ods-restore.sh is not executable"
restore_fn="$(sed -n '/^restore_user_data() {/,/^}$/p' "$RESTORE")"

grep -q 'stage_dir=' <<<"$restore_fn" || fail "no staging directory"
grep -q 'Staging \$dir' <<<"$restore_fn" || fail "no staged copy"
grep -q 'activated_dirs=' <<<"$restore_fn" || fail "no activation tracking"
grep -q 'rollback_dir=' <<<"$restore_fn" || fail "no rollback state"
grep -q 'Failed to stage user data' <<<"$restore_fn" || fail "no staging failure handling"

activation_line="$(grep -n 'local target=' <<<"$restore_fn" | cut -d: -f1)"
staging_line="$(grep -n 'Staging \$dir' <<<"$restore_fn" | cut -d: -f1)"
(( staging_line < activation_line )) || fail "activation precedes staging"

pass "restore stages user data before transactional activation"
