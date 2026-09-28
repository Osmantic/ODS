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
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' > "$FIXTURE/.compose-flags"
mkdir -p "$FIXTURE/config/litellm"
printf 'model_list: []\n' > "$FIXTURE/config/litellm/config.yaml"
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
# 2 compose files + .env + .compose-flags + config/litellm + .version = 6
if echo "$output" | grep -q "Files backed up: 6"; then
    pass "backup counted all 6 files"
else
    fail "wrong file count: $(echo "$output" | grep 'Files backed up' || echo "$output")"
fi
if [[ -f "$backup_dir/docker-compose.nvidia.yml" && -f "$backup_dir/.version" ]]; then
    pass "backup copied files beyond the first one"
else
    fail "backup stopped after the first file"
fi
if [[ -f "$backup_dir/.compose-flags" && -f "$backup_dir/config-litellm/config.yaml" \
      && -f "$backup_dir/metadata.json" && -f "$backup_dir/snapshot.json" ]] \
   && ! find "$backup_dir" -mindepth 1 -maxdepth 1 -name '.*.tmp.*' | grep -q .; then
    pass "backup publishes a flat, restorable snapshot with active overlays and config"
else
    fail "backup published missing or nested snapshot content: $output"
fi

# ---------------------------------------------------------------------------
# 3. rotation prunes down to MAX_BACKUPS without aborting
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS"
# Name them the way cmd_backup does: backup-<YYYYmmdd-HHMMSS>.
for i in 01 02 03 04 05; do
    mkdir -p "$BACKUPS/backup-2020010${i#0}-00000${i#0}"
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

# ---------------------------------------------------------------------------
# 4. backup labels cannot escape the backup directory
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS"
output=$(run_backup "../../../escaped")

if echo "$output" | grep -q "Invalid backup name" && \
        ! find "$FIXTURE/home/.ods" -type d -name "escaped-*" | grep -q .; then
    pass "backup rejects path-like labels before writing"
else
    fail "path-like backup label escaped or was accepted: $output"
fi

# ---------------------------------------------------------------------------
# 5. failed backups never become visible rollback candidates
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS"
FAIL_BIN="$FIXTURE/fail-bin"
mkdir -p "$FAIL_BIN"
printf '#!/bin/sh\nexit 17\n' > "$FAIL_BIN/cp"
chmod +x "$FAIL_BIN/cp"
output=$(PATH="$FAIL_BIN:$PATH" run_backup broken)

if ! find "$BACKUPS" -maxdepth 1 -type d -name "backup-broken-*" | grep -q . && \
        ! find "$BACKUPS" -maxdepth 1 -type d -name ".backup-broken-*" | grep -q .; then
    pass "failed backup leaves no final or staging directory"
else
    remaining_failed=$(find "$BACKUPS" -maxdepth 1 -type d -name "*backup-broken-*" -print)
    fail "failed backup remained selectable ($remaining_failed): $output"
fi

# ---------------------------------------------------------------------------
# 6. a repeated backup id cannot overwrite a completed snapshot
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS"
DATE_BIN="$FIXTURE/date-bin"
mkdir -p "$DATE_BIN"
# shellcheck disable=SC2016  # ${1:-} belongs to the generated date stub.
printf '%s\n' \
    '#!/bin/sh' \
    'if [ "${1:-}" = "-u" ]; then' \
    '  echo 2026-08-23T12:34:56Z' \
    'else' \
    '  echo 20260823-123456' \
    'fi' > "$DATE_BIN/date"
chmod +x "$DATE_BIN/date"

first_output=$(PATH="$DATE_BIN:$PATH" run_backup collision)
echo '{"version": "9.9.9"}' > "$FIXTURE/.version"
second_output=$(PATH="$DATE_BIN:$PATH" run_backup collision)
collision_count=$(find "$BACKUPS" -maxdepth 1 -type d -name "backup-collision-*" | wc -l)
collision_dir=$(find "$BACKUPS" -maxdepth 1 -type d -name "backup-collision-*" | head -1)

if [[ "$collision_count" -eq 1 ]] && \
        grep -q '"version": "2.0.0"' "$collision_dir/.version" && \
        echo "$second_output" | grep -Eq "already exists|already in progress"; then
    pass "repeated backup id preserves the completed snapshot"
else
    fail "repeated backup id overwrote or duplicated the snapshot: first=$first_output second=$second_output"
fi

# ---------------------------------------------------------------------------
# 7. an in-progress backup id rejects a concurrent writer
# ---------------------------------------------------------------------------
rm -rf "$BACKUPS"
mkdir -p "$BACKUPS/.backup-concurrent-20260823-123456.lock"
output=$(PATH="$DATE_BIN:$PATH" run_backup concurrent)

if echo "$output" | grep -q "Backup already in progress" && \
        ! find "$BACKUPS" -maxdepth 1 -type d -name "backup-concurrent-*" | grep -q .; then
    pass "backup lock rejects a concurrent writer"
else
    fail "backup ignored an active transaction lock: $output"
fi

# ---------------------------------------------------------------------------
# 8. invalid retention cannot delete the snapshot and report success
# ---------------------------------------------------------------------------
for invalid_retention in 0 -1 invalid; do
    if HOME="$FIXTURE/home" MAX_BACKUPS="$invalid_retention" \
        bash "$FIXTURE/ods-update.sh" backup retention > "$FIXTURE/retention.log" 2>&1; then
        fail "MAX_BACKUPS=$invalid_retention was accepted"
    elif grep -q 'MAX_BACKUPS must be a positive integer' "$FIXTURE/retention.log" \
        && ! find "$BACKUPS" -maxdepth 1 -type d -name 'backup-retention-*' | grep -q .; then
        pass "MAX_BACKUPS=$invalid_retention refuses an unusable snapshot"
    else
        fail "MAX_BACKUPS=$invalid_retention failed without the retention guard"
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
