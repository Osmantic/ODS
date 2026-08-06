#!/usr/bin/env bash
# ============================================================================
# rsync_with_progress --delete opt-in contract test
# ============================================================================
# Regression for issue #2304: rsync_with_progress baked --delete into every
# rsync call. In the backup flow that meant each service/config/cache rsync
# into the SAME backup dir deleted the entries the previous call had just
# written (only the last path survived), and on restore the --delete against
# the live data tree wiped the other service directories.
#
# Strategy: install an rsync shim that records the exact argv so we can assert
# --delete is ABSENT by default and only present when the caller opts in.
#
# Usage: ./tests/test-rsync-delete-optin.sh
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RSYNC_LIB="$ROOT_DIR/lib/rsync.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

[[ -f "$RSYNC_LIB" ]] || { echo "rsync.sh not found at $RSYNC_LIB"; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/rsync-delete.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

CALL_LOG="$TMP/rsync-args.log"
mkdir -p "$TMP/bin"

cat > "$TMP/bin/rsync" <<SH
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$CALL_LOG"
# Fail unless --help: this test asserts argv, not copy behavior.
exit 0
SH
chmod +x "$TMP/bin/rsync"

export PATH="$TMP/bin:$PATH"

# ---- helper: source the lib inside a subshell that stubs log_info ---------
run_rsync() {
    local out="$1"; shift
    ( log_info() { :; }; . "$RSYNC_LIB"; rsync_with_progress "$@" ) > "$out" 2>&1
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   rsync_with_progress --delete opt-in test    ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. default call (backup/restore callers) must NOT pass --delete
# ---------------------------------------------------------------------------
: > "$CALL_LOG"
run_rsync "$TMP/default.log" /src/data /backup/data/ "label"
if grep -q -- "--delete" "$CALL_LOG"; then
    fail "default rsync_with_progress must not pass --delete"
else
    pass "default rsync_with_progress omits --delete"
fi
if grep -q -- "-a" "$CALL_LOG"; then
    pass "default rsync_with_progress keeps -a archive mode"
else
    fail "default rsync_with_progress lost -a archive mode"
fi

# ---------------------------------------------------------------------------
# 2. explicit opt-in must pass --delete (mirror-sync caller)
# ---------------------------------------------------------------------------
: > "$CALL_LOG"
run_rsync "$TMP/optin.log" /src/data /mirror/data/ "label" "--delete"
if grep -q -- "--delete" "$CALL_LOG"; then
    pass "rsync_with_progress forwards --delete when explicitly opted in"
else
    fail "rsync_with_progress dropped the --delete opt-in flag"
fi

# ---------------------------------------------------------------------------
# 3. a non---delete 4th arg must not inject --delete (defensive)
# ---------------------------------------------------------------------------
: > "$CALL_LOG"
run_rsync "$TMP/junk.log" /src/data /mirror/data/ "label" "junk"
if grep -q -- "--delete" "$CALL_LOG"; then
    fail "unrecognized 4th arg must not be treated as --delete"
else
    pass "unrecognized 4th arg is ignored (no --delete)"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
