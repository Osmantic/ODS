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
# ============================================================================

# Rsync with progress indicator
# Args:
#   $1 - source path
#   $2 - destination path
#   $3 - optional label (default: "Copying")
#   $4 - optional extra rsync flags, e.g. "--delete" (default: none)
#
# --delete is NOT enabled by default. It makes the destination an exact
# mirror of the source, removing anything under dest that isn't in src —
# including files that were created in a live dest directory *after* the
# source snapshot was taken. ods-restore.sh's restore_user_data() rsyncs an
# (older) backup dir over a live data dir; with --delete this silently
# deleted any file the user created since the backup ran, contradicting the
# function's own "preserve files created after backup" comment. Pass
# "--delete" explicitly only at call sites that truly want a mirror sync.
rsync_with_progress() {
    local src="$1"
    local dest="$2"
    local label="${3:-Copying}"
    local extra_flags="${4:-}"

    # Prefer the caller's styled logger when it exists. log_info is a *function*
    # in the scripts that source this lib (ods-backup.sh, ods-restore.sh), so it
    # must be probed with `declare -F`, not `${log_info:-}` (which only ever sees
    # a variable and is always empty — the styled path was previously dead code).
    if declare -F log_info >/dev/null 2>&1; then
        log_info "$label..."
    else
        echo "[INFO] $label..."
    fi

    local -a extra_args=()
    if [[ -n "$extra_flags" ]]; then
        # shellcheck disable=SC2206  # intentional word-splitting of flag string
        extra_args=($extra_flags)
    fi

    # Use --info=progress2 for compact single-line progress updates
    # Fallback to basic rsync if progress2 not supported
    if rsync --help 2>/dev/null | grep -q "info=progress2"; then
        rsync -a "${extra_args[@]}" --info=progress2 "$src" "$dest"
    else
        # Fallback: use --progress for older rsync versions
        rsync -a "${extra_args[@]}" --progress "$src" "$dest" 2>/dev/null || rsync -a "${extra_args[@]}" "$src" "$dest"
    fi
}
