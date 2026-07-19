#!/bin/bash
# ============================================================================
# ods-update.sh backup command test
# ============================================================================
# cmd_backup counted copied files with ((files_backed_up++)). Bash
# post-increment evaluates to the old value, so the first increment
# (0 -> 1) returns status 1 and set -e killed the script right after
# copying the first compose file: no metadata.json, no "Backup created",
# exit 1. Since ods-cli's cmd_update delegates its pre-update snapshot to
# `ods-update.sh backup`, the safety net always failed with
# "Pre-update snapshot failed; proceeding without safety net."
# The rotation loop's ((count++)) had the same defect.
#
# Strategy: run the real script from a throwaway install dir with HOME
# pointed at a fixture so BACKUP_DIR ($HOME/.ods/backups) is isolated.
#
# Usage: ./tests/test-update-script-backup.sh
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

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-update-backup.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

# INSTALL_DIR is the script's own directory; BACKUP_DIR is $HOME/.ods/backups
mkdir -p "$FIXTURE/home"
cp "$ROOT_DIR/ods-update.sh" "$FIXTURE/ods-update.sh"
: > "$FIXTURE/docker-compose.base.yml"
: > "$FIXTURE/docker-compose.nvidia.yml"
echo "GPU_BACKEND=nvidia" > "$FIXTURE/.env"
echo '{"version": "2.0.0"}' > "$FIXTURE/.version"

BACKUPS="$FIXTURE/home/.ods/backups"

run_backup() {
    # Never let a non-zero exit kill the test; callers assert on output/state
    HOME="$FIXTURE/home" bash "$FIXTURE/ods-update.sh" backup "$@" 2>&1 || true
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   ods-update.sh backup test                   ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. backup completes past the first copied file
# ---------------------------------------------------------------------------
output=$(run_backup snaptest)
backup_dir=$(find "$BACKUPS" -maxdepth 1 -type d -name "backup-snaptest-*" | head -1)

if echo "$output" | grep -q "Backup created"; then
    pass "backup runs to completion"
else
    fail "backup aborted mid-copy: $output"
fi
if [[ -n "$backup_dir" && -f "$backup_dir/metadata.json" ]]; then
    pass "backup wrote metadata.json"
else
    fail "metadata.json missing (backup died before writing it)"
fi

# ---------------------------------------------------------------------------
# 2. all eligible files are copied and counted
# ---------------------------------------------------------------------------
# 2 compose files + .env + .version = 4
if echo "$output" | grep -q "Files backed up: 4"; then
    pass "backup counted all 4 files"
else
    fail "wrong file count: $(echo "$output" | grep 'Files backed up' || echo "$output")"
fi
if [[ -f "$backup_dir/docker-compose.nvidia.yml" && -f "$backup_dir/.version" ]]; then
    pass "backup copied files beyond the first one"
else
    fail "backup stopped after the first file"
fi

# ---------------------------------------------------------------------------
# 3. rotation prunes down to MAX_BACKUPS without aborting
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS"
for i in 01 02 03 04 05; do
    mkdir -p "$BACKUPS/backup-2020010${i#0}-00000$i"
done

output=$(MAX_BACKUPS=3 run_backup rotate)
remaining=$(find "$BACKUPS" -maxdepth 1 -type d -name "backup-*" | wc -l)

if [[ "$remaining" -eq 3 ]]; then
    pass "rotation kept exactly MAX_BACKUPS backups"
else
    fail "rotation left $remaining backups (expected 3): $output"
fi
if find "$BACKUPS" -maxdepth 1 -type d -name "backup-rotate-*" | grep -q .; then
    pass "newest backup survived rotation"
else
    fail "rotation removed the backup it just created"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
