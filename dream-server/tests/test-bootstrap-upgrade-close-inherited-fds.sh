#!/usr/bin/env bash
# Regression: every spawn site that launches scripts/bootstrap-upgrade.sh as a
# long-lived nohup background daemon MUST close inherited file descriptors
# 3-9 in its redirection list. Otherwise the daemon holds any flock its
# caller opened (e.g. a fleet-test harness FD 9 advisory lock) for the full
# lifetime of the background model download.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

assert_fd_close_block() {
    local target="$1"
    local label="$2"

    [[ -f "$target" ]] || fail "missing $target"

    # Pull every line of the bootstrap-upgrade.sh spawn block (the nohup
    # invocation through its stdout/stderr redirect), strip comments, and
    # assert the FD-close redirections sit inside it. Comments are stripped
    # so a description of the fix can't satisfy or fail the test.
    local block
    block="$(awk '
        /scripts\/bootstrap-upgrade.sh/ { in_block=1 }
        in_block { print }
        in_block && /model-upgrade\.log.*2>&1.*&/ { exit }
    ' "$target" | grep -v '^[[:space:]]*#')"

    [[ -n "$block" ]] || fail "$label: could not locate bootstrap-upgrade.sh spawn block"

    local fd
    for fd in 3 4 5 6 7 8 9; do
        grep -qF "${fd}>&-" <<<"$block" \
            || fail "$label: nohup bootstrap-upgrade.sh spawn must close inherited FD ${fd} (missing '${fd}>&-')"
    done
    pass "$label: bootstrap-upgrade.sh spawn closes inherited FDs 3-9"
}

assert_fd_close_block "$ROOT_DIR/installers/phases/11-services.sh"     "linux/wsl phase 11"
assert_fd_close_block "$ROOT_DIR/installers/macos/install-macos.sh"   "macos installer"

echo "[OK] all bootstrap-upgrade spawn sites close inherited FDs"
