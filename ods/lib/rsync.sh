#!/bin/bash
# ============================================================================
# ODS — Rsync Utilities
# ============================================================================
# Part of: lib/
# Purpose: Shared rsync functions with progress indicators
#
# Expects: None (standalone utility)
# Provides: rsync_with_progress()
#
# Usage:
#   . "$ODS_DIR/lib/rsync.sh"
#   rsync_with_progress "$src" "$dest" "Optional label"
#   rsync_with_progress "$src" "$dest" "Optional label" "--delete"  # mirror-sync ONLY
# ============================================================================

# Rsync with progress indicator
# Args:
#   $1 - source path
#   $2 - destination path
#   $3 - optional label (default: "Copying")
#   $4 - optional "--delete"; ONLY for a true mirror-sync into a dedicated
#        destination. --delete removes every file the source does not contain,
#        so it must never be used when the destination already holds unrelated
#        content: a backup dir receives one rsync per service/config/cache path
#        (--delete would wipe the previously backed-up entries), and restore
#        writes into the live data tree (--delete would delete the other live
#        service dirs). The flag is deliberately opt-in and unused by the
#        current backup/restore callers.
rsync_with_progress() {
    local src="$1"
    local dest="$2"
    local label="${3:-Copying}"
    local delete_flag="${4:-}"

    # Prefer the caller's styled logger when it exists. log_info is a *function*
    # in the scripts that source this lib (ods-backup.sh, ods-restore.sh), so it
    # must be probed with `declare -F`, not `${log_info:-}` (which only ever sees
    # a variable and is always empty — the styled path was previously dead code).
    if declare -F log_info >/dev/null 2>&1; then
        log_info "$label..."
    else
        echo "[INFO] $label..."
    fi

    local rsync_args=(-a)
    [[ "$delete_flag" == "--delete" ]] && rsync_args+=(--delete)

    # Use --info=progress2 for compact single-line progress updates
    # Fallback to basic rsync if progress2 not supported
    if rsync --help 2>/dev/null | grep -q "info=progress2"; then
        rsync "${rsync_args[@]}" --info=progress2 "$src" "$dest"
    else
        # Fallback: use --progress for older rsync versions
        rsync "${rsync_args[@]}" --progress "$src" "$dest" 2>/dev/null || rsync "${rsync_args[@]}" "$src" "$dest"
    fi
}
