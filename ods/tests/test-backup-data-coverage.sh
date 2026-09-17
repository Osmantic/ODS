#!/usr/bin/env bash
# Every ./data/ directory a bundled service persists must be a deliberate
# backup decision: captured by `ods backup`, or listed as an excluded cache.
#
# The backup path list used to be copied into five places across
# ods-backup.sh and ods-restore.sh, and it stopped tracking the services that
# were added around it. This test pins the contract to the compose files.
#
# Run from ods/:  bash tests/test-backup-data-coverage.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_DIR="$ROOT_DIR"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

# shellcheck source=/dev/null
. "$ROOT_DIR/lib/backup-paths.sh"

[[ ${#ODS_USER_DATA_PATHS[@]} -gt 0 ]] || fail "ODS_USER_DATA_PATHS is empty"
[[ ${#ODS_BACKUP_EXCLUDED_DATA_PATHS[@]} -gt 0 ]] || fail "ODS_BACKUP_EXCLUDED_DATA_PATHS is empty"

# Top-level ./data/<dir> bind mounts declared by the bundled stack. Library
# extensions are installed on demand and manage their own data, so they are
# out of scope here.
mounted_data_dirs() {
    {
        find "$ROOT_DIR" -maxdepth 1 -name 'docker-compose*.yml' -print0
        find "$ROOT_DIR/extensions/services" -name 'compose*.yaml' -print0
    } | xargs -0 grep -ho -- '\./data/[A-Za-z0-9._-]\+' 2>/dev/null \
        | sed 's|^\./||' \
        | sort -u
}

covered=" ${ODS_USER_DATA_PATHS[*]} ${ODS_BACKUP_EXCLUDED_DATA_PATHS[*]} "

uncovered=""
while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    if [[ "$covered" != *" $dir "* ]]; then
        uncovered+="$dir "
    fi
done < <(mounted_data_dirs)

[[ -z "$uncovered" ]] \
    || fail "service data neither backed up nor explicitly excluded: ${uncovered% }"
pass "every bundled service data directory is a deliberate backup decision"

# The two lists must stay disjoint, or a directory is both captured and
# documented as skipped.
for excluded in "${ODS_BACKUP_EXCLUDED_DATA_PATHS[@]}"; do
    for included in "${ODS_USER_DATA_PATHS[@]}"; do
        [[ "$excluded" != "$included" ]] \
            || fail "$excluded appears in both the backup list and the exclusion list"
    done
done
pass "backup and exclusion lists are disjoint"

# Both scripts must read the shared array rather than re-inlining a copy.
for script in ods-backup.sh ods-restore.sh; do
    grep -q 'lib/backup-paths.sh' "$ROOT_DIR/$script" \
        || fail "$script does not source lib/backup-paths.sh"
    grep -q 'ODS_USER_DATA_PATHS\[@\]' "$ROOT_DIR/$script" \
        || fail "$script does not use ODS_USER_DATA_PATHS"
    if grep -qE '(local|local -a)[^=]*=\(\s*"data/open-webui"' "$ROOT_DIR/$script"; then
        fail "$script re-inlines a literal user-data path list"
    fi
done
pass "ods-backup.sh and ods-restore.sh share one path list"

echo "[PASS] backup data coverage"
