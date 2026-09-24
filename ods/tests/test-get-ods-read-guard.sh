#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: get-ods.sh's "Remove and reinstall?" prompt must not
# die silently when stdin is a spent pipe — the script's own install
# method is `curl | bash`, where stdin reaches EOF before the prompt.
# Under `set -euo pipefail`, a bare `read` returning EOF killed the
# script immediately after printing the question, with no abort message
# and no tty chance to answer.
#
# The fix reads the answer from /dev/tty (so the prompt still works
# under curl|bash) and falls back to an empty response — the abort
# branch — when no terminal exists.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/get-ods.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Fixture: install dir exists but has no .env -> "incomplete install" path.
export ODS_BOOTSTRAP_ROOT="$TMP_ROOT"
export ODS_INSTALL_DIR="$TMP_ROOT/ods"
mkdir -p "$ODS_INSTALL_DIR"

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== get-ods reinstall prompt under EOF stdin ==="

# stdin = /dev/null simulates the exhausted curl|bash pipe. setsid drops
# the controlling terminal so /dev/tty cannot satisfy the prompt — the
# fixed script must take the abort branch instead of dying on read.
set +e
out="$(setsid bash "$SCRIPT" </dev/null 2>&1)"
rc=$?
set -e

[[ "$rc" -ne 0 ]] || fail "script exited 0 — expected the abort path (rc=$rc)"

grep -q "Remove and reinstall?" <<<"$out" \
    || fail "prompt was never printed (out: $(tail -3 <<<"$out"))"

# The abort message proves control reached the branch — on the broken
# version `read` kills the script right after the prompt, before this.
grep -q "Aborting. Remove manually" <<<"$out" \
    || fail "script died at the read instead of reaching the abort branch (out: $(tail -4 <<<"$out"))"

# And the incomplete install dir must still be there.
[[ -d "$ODS_INSTALL_DIR" ]] \
    || fail "install dir was removed without confirmation"

echo "PASS: EOF stdin reaches the abort branch and preserves the install dir"
echo "PASS: all get-ods read-guard cases"
