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
#   rsync_with_progress "$src" "$dest" "Optional label" delete
# ============================================================================

# Rsync with progress indicator
# Args:
#   $1 - source path
#   $2 - destination path
#   $3 - optional label (default: "Copying")
#   $4 - optional "delete" to enable rsync --delete (true mirror sync).
#        Alternatively set RSYNC_DELETE=1 in the environment.
# --delete is opt-in, never the default: backup/restore flows rely on it
# being absent so destination data is never wiped.
rsync_with_progress() {
    local src="$1"
    local dest="$2"
    local label="${3:-Copying}"
    local delete="${4:-}"

    local -a delete_args=()
    if [[ "$delete" == "delete" || "${RSYNC_DELETE:-0}" == "1" ]]; then
        delete_args+=( --delete )
    fi

    # Prefer the caller's styled logger when it exists. log_info is a *function*
    # in the scripts that source this lib (ods-backup.sh, ods-restore.sh), so it
    # must be probed with `declare -F`, not `${log_info:-}` (which only ever sees
    # a variable and is always empty — the styled path was previously dead code).
    if declare -F log_info >/dev/null 2>&1; then
        log_info "$label..."
    else
        echo "[INFO] $label..."
    fi

    # Use --info=progress2 for compact single-line progress updates
    # Fallback to basic rsync if progress2 not supported
    if rsync --help 2>/dev/null | grep -q "info=progress2"; then
        rsync -a "${delete_args[@]}" --info=progress2 "$src" "$dest"
    else
        # Fallback: use --progress for older rsync versions
        rsync -a "${delete_args[@]}" --progress "$src" "$dest" 2>/dev/null || rsync -a "${delete_args[@]}" "$src" "$dest"
    fi
}
