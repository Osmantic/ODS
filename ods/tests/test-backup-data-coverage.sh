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

# dashboard-api mounts the whole ./data tree (./data:/data) and writes state
# straight into it, so the bind-mount scan above cannot see those paths.
# Find every top-level name it joins onto its data root (DATA_DIR / "x",
# data_dir / "x", "/data")) / "x", "/data/x") in non-test code. Dot-prefixed
# lock files are runtime coordination, never state.
DASHBOARD_API_DIR="$ROOT_DIR/extensions/services/dashboard-api"
[[ -d "$DASHBOARD_API_DIR" ]] || fail "dashboard-api source not found at $DASHBOARD_API_DIR"
dashboard_api_data_paths() {
    find "$DASHBOARD_API_DIR" -name '*.py' -not -path '*/tests/*' -print0 \
        | xargs -0 grep -hoE \
            -e '(DATA_DIR|data_dir|"/data")\)*[[:space:]]*/[[:space:]]*["'"'"'][A-Za-z0-9._-]+' \
            -e '"/data/[A-Za-z0-9._-]+' \
        | sed -E 's|.*["'"'"'/]||' \
        | grep -v '^\.' \
        | sed 's|^|data/|' \
        | sort -u
}

api_paths="$(dashboard_api_data_paths)"
[[ "$api_paths" == *"data/dashboard-password.json"* ]] \
    || fail "dashboard-api data path scan found nothing; the scan pattern is stale"
uncovered=""
while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if [[ "$covered" != *" $path "* ]]; then
        uncovered+="$path "
    fi
done <<< "$api_paths"
[[ -z "$uncovered" ]] \
    || fail "dashboard-api data neither backed up nor explicitly excluded: ${uncovered% }"
pass "every dashboard-api data path is a deliberate backup decision"

# The two lists must stay disjoint, or a directory is both captured and
# documented as skipped.
for excluded in "${ODS_BACKUP_EXCLUDED_DATA_PATHS[@]}"; do
    for included in "${ODS_USER_DATA_PATHS[@]}"; do
        [[ "$excluded" != "$included" ]] \
            || fail "$excluded appears in both the backup list and the exclusion list"
    done
done
pass "backup and exclusion lists are disjoint"

# The cache tier (`ods backup -t full`) may only hold directories the default
# backup deliberately skips; anything else would be captured twice or would
# turn a skipped directory into a silently forgotten one.
[[ ${#ODS_BACKUP_CACHE_PATHS[@]} -gt 0 ]] || fail "ODS_BACKUP_CACHE_PATHS is empty"
for cached in "${ODS_BACKUP_CACHE_PATHS[@]}"; do
    [[ " ${ODS_BACKUP_EXCLUDED_DATA_PATHS[*]} " == *" $cached "* ]] \
        || fail "$cached is in the cache tier but not in the exclusion list"
done
pass "the full-backup cache tier only holds deliberately excluded directories"

# Both scripts must read the shared array rather than re-inlining a copy.
for script in ods-backup.sh ods-restore.sh; do
    grep -q 'lib/backup-paths.sh' "$ROOT_DIR/$script" \
        || fail "$script does not source lib/backup-paths.sh"
    grep -q 'ods_backup_data_paths' "$ROOT_DIR/$script" \
        || fail "$script does not use ods_backup_data_paths"
    grep -q 'ODS_BACKUP_CACHE_PATHS\[@\]' "$ROOT_DIR/$script" \
        || fail "$script does not use ODS_BACKUP_CACHE_PATHS"
    if grep -qE '(local|local -a)[^=]*=\(\s*"data/open-webui"' "$ROOT_DIR/$script"; then
        fail "$script re-inlines a literal user-data path list"
    fi
done
pass "ods-backup.sh and ods-restore.sh share one path list"

echo "[PASS] backup data coverage"
