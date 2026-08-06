#!/bin/bash
# ============================================================================
# collect_backups backup-ID regex test
# ============================================================================
# Regression for issue #2299: collect_backups only accepted at most one
# hyphenated prefix segment, so a user-named backup like
# dashboard-my-name-20260715-143022 was invisible to list_backups and
# apply_retention (though still deletable). The host-agent's BACKUP_ID_RE
# permits [A-Za-z0-9_-]{0,63} labels, so the two sides disagreed.
#
# Strategy: extract the regex literal from ods-backup.sh (the script itself
# needs rsync, so we don't source it) and assert it matches multi-segment
# backup IDs while still rejecting operator files that don't carry the
# trailing YYYYMMDD-HHMMSS timestamp.
#
# Usage: ./tests/test-backup-id-regex.sh
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

# Pull the regex literal out of collect_backups so the test always reflects
# the shipped pattern (no copy-paste drift).
backup_id_re=$(sed -n 's/.*\[\[ "\$base" =~ \(.*\) \]\] || continue/\1/p' "$ROOT_DIR/ods-backup.sh")

if [[ -z "$backup_id_re" ]]; then
    echo -e "  ${RED}✗ FAIL${NC} could not extract backup-ID regex from ods-backup.sh"
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   collect_backups backup-ID regex test        ║"
echo "╚═══════════════════════════════════════════════╝"
echo "  regex: $backup_id_re"
echo ""

# Names that must match (backup dirs / archives this tool should manage).
should_match=(
    "20260715-143022"
    "20260715-143022.tar.gz"
    "dashboard-20260715-143022"
    "dashboard-my-name-20260715-143022"
    "dashboard-my-name-20260715-143022.tar.gz"
    "my_name-backup-20260715-143022"
    "a-b-c-20260715-143022"
)

# Names that must NOT match (operator files must never qualify for retention).
should_reject=(
    "notes.txt"
    "my-file.txt"
    "20260715"
    "20260715-143022.zip"
    "dashboard-my-name"
    "dashboard-my-name-20260715"
    ".bashrc"
    "backup"
    "readme"
)

for name in "${should_match[@]}"; do
    if [[ "$name" =~ $backup_id_re ]]; then
        pass "accepts multi-segment backup ID: $name"
    else
        fail "REJECTED backup ID that must match: $name"
    fi
done

for name in "${should_reject[@]}"; do
    if [[ "$name" =~ $backup_id_re ]]; then
        fail "MATCHED operator file that must be excluded: $name"
    else
        pass "rejects non-backup file: $name"
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
