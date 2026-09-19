#!/bin/bash
# ============================================================================
# ods-update.sh backup same-second collision test
# ============================================================================
# cmd_backup and snapshot_pre_update named their directories
# backup-<label?>-<YYYYMMDD-HHMMSS> / pre-update-<YYYYMMDD-HHMMSS>. Two
# invocations inside one wall-clock second (a scripted `ods update` retry,
# `ods backup` run twice by automation, or update's snapshot immediately
# followed by a manual backup) collided on a single directory and both
# wrote into it — silently merging two snapshots.
#
# Strategy: stub `date` so the stamp is pinned to one fixed second, then run
# the real script from a throwaway install dir with HOME pointed at a
# fixture so BACKUP_DIR ($HOME/.ods/backups) is isolated.
#
# Usage: ./tests/test-backup-same-second-collision.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-backup-collision.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/home" "$FIXTURE/bin"
cp "$ROOT_DIR/ods-update.sh" "$FIXTURE/ods-update.sh"
: > "$FIXTURE/docker-compose.base.yml"
echo "GPU_BACKEND=nvidia" > "$FIXTURE/.env"
echo '{"version": "2.0.0"}' > "$FIXTURE/.version"

# Pin every backup timestamp to one second so collisions are deterministic.
cat > "$FIXTURE/bin/date" <<'EOF'
#!/bin/bash
if [[ "$*" == "+%Y%m%d-%H%M%S" ]]; then
    echo "20260101-000000"
else
    # Delegate to the real binary by absolute path — `command date` would
    # re-resolve this stub and recurse forever.
    /bin/date "$@" 2>/dev/null || /usr/bin/date "$@"
fi
EOF
chmod +x "$FIXTURE/bin/date"

BACKUPS="$FIXTURE/home/.ods/backups"

run_backup() {
    HOME="$FIXTURE/home" PATH="$FIXTURE/bin:$PATH" \
        bash "$FIXTURE/ods-update.sh" backup "$@" 2>&1 || true
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   ods-update.sh same-second collision test    ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. Two unlabelled backups in the same second produce two directories
run_backup >/dev/null
run_backup >/dev/null

if [[ -d "$BACKUPS/backup-20260101-000000" && -d "$BACKUPS/backup-20260101-000000-2" ]]; then
    pass "second same-second backup gets a -2 suffix instead of merging"
else
    fail "same-second backups did not disambiguate: $(ls "$BACKUPS" 2>/dev/null | tr '\n' ' ')"
fi

# 2. Third backup continues the sequence
run_backup >/dev/null
if [[ -d "$BACKUPS/backup-20260101-000000-3" ]]; then
    pass "third same-second backup gets -3"
else
    fail "missing -3 backup dir"
fi

# 3. Each directory has its own snapshot metadata (no merge/overwrite)
if [[ -f "$BACKUPS/backup-20260101-000000/snapshot.json" \
   && -f "$BACKUPS/backup-20260101-000000-2/snapshot.json" ]]; then
    pass "each collided backup wrote its own snapshot.json"
else
    fail "snapshot.json missing in a suffixed backup dir"
fi

# 4. Labelled backups disambiguate too
run_backup nightly >/dev/null
run_backup nightly >/dev/null
if [[ -d "$BACKUPS/backup-nightly-20260101-000000" \
   && -d "$BACKUPS/backup-nightly-20260101-000000-2" ]]; then
    pass "labelled same-second backups get distinct dirs"
else
    fail "labelled collision not handled"
fi

# 5. Name parsers still match: suffixed names keep a trailing stamp regex hit
if [[ "backup-20260101-000000-2" =~ -([0-9]{8}-[0-9]{6})(-[0-9]+)?$ ]]; then
    pass "suffixed backup name still parses as timestamped"
else
    fail "suffixed name breaks the stamp regex contract"
fi

echo ""
echo "════════════════════════════════════════════════"
echo -e "Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC}"
echo "════════════════════════════════════════════════"

exit "$FAILED"
