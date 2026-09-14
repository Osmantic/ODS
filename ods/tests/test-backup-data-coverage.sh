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

# Top-level data/<dir> bind mounts declared by the bundled stack. Accept both
# literal ./data and the configurable ${ODS_DATA_DIR:-./data} form, including
# disabled Compose definitions for dormant built-in services. Library
# extensions are installed on demand and manage their own data, so they remain
# out of scope here.
mounted_data_dirs() {
    {
        find "$ROOT_DIR" -maxdepth 1 \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' \) -print0
        find "$ROOT_DIR/extensions/services" \
            \( -name 'compose*.yaml' -o -name 'compose*.yml' \
               -o -name 'compose*.yaml.disabled' -o -name 'compose*.yml.disabled' \) -print0
    } | xargs -0 grep -Eho -- '(\./data|\$\{ODS_DATA_DIR:-\./data\})/[A-Za-z0-9._-]+' 2>/dev/null \
        | sed -E 's|^\$\{ODS_DATA_DIR:-\./data\}/|data/|; s|^\./||' \
        | sort -u
}

data_dir_is_declared() {
    local directory="$1" path
    for path in \
        "${ODS_USER_DATA_PATHS[@]}" \
        "${ODS_BACKUP_EXCLUDED_DATA_PATHS[@]}" \
        "${ODS_BACKUP_EXCLUDED_SECRET_PATHS[@]}" \
        "${ODS_BACKUP_EXCLUDED_TRANSIENT_PATHS[@]}"; do
        [[ "$path" == "$directory" || "$path" == "$directory/"* ]] && return 0
    done
    return 1
}

uncovered=""
while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    if ! data_dir_is_declared "$dir"; then
        uncovered+="$dir "
    fi
done < <(mounted_data_dirs)

[[ -z "$uncovered" ]] \
    || fail "service data neither backed up nor explicitly excluded: ${uncovered% }"
pass "every bundled service data directory is a deliberate backup decision"

# The two lists must stay disjoint, or a directory is both captured and
# documented as skipped.
for excluded in \
    "${ODS_BACKUP_EXCLUDED_DATA_PATHS[@]}" \
    "${ODS_BACKUP_EXCLUDED_SECRET_PATHS[@]}" \
    "${ODS_BACKUP_EXCLUDED_TRANSIENT_PATHS[@]}"; do
    for included in "${ODS_USER_DATA_PATHS[@]}"; do
        [[ "$excluded" != "$included" ]] \
            || fail "$excluded appears in both the backup list and the exclusion list"
    done
done
pass "backup and exclusion lists are disjoint"

# Assistant First control-plane state is not discovered from Compose mounts,
# so pin it explicitly. Secret custody stays host-owned and is not placed in a
# normal unencrypted backup archive.
for required in \
    data/assistant-first/desired-state \
    data/assistant-first/transaction-store \
    data/user-extensions \
    data/remote-provider/routing-state.json \
    data/pixel-inference; do
    [[ " ${ODS_USER_DATA_PATHS[*]} " == *" $required "* ]] \
        || fail "Assistant First control-plane state is not backed up: $required"
done
[[ " ${ODS_USER_DATA_PATHS[*]} " != *" data/assistant-first/secrets "* ]] \
    || fail "host-owned Assistant First secrets must not enter ordinary backups"
for secret_path in \
    data/assistant-first/secrets \
    data/remote-provider/secrets \
    data/dashboard-api-key.txt \
    data/config-backups; do
    [[ " ${ODS_BACKUP_EXCLUDED_SECRET_PATHS[*]} " == *" $secret_path "* ]] \
        || fail "secret-bearing path lacks an explicit exclusion: $secret_path"
done
[[ " ${ODS_BACKUP_EXCLUDED_TRANSIENT_PATHS[*]} " == *" data/user-extensions/.tmp "* ]] \
    || fail "user-extension staging state lacks an explicit transient exclusion"
pass "durable control state is covered with explicit secret and transient exclusions"

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
