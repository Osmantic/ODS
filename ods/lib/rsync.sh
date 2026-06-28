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
rsync_with_progress() {
    local src="$1"
    local dest="$2"
    local label="${3:-Copying}"

    [[ -n "${log_info:-}" ]] && log_info "$label..." || echo "[INFO] $label..."

    # Use --info=progress2 for compact single-line progress updates
    # Fallback to basic rsync if progress2 not supported
    if rsync --help 2>/dev/null | grep -q "info=progress2"; then
        rsync -a --delete --info=progress2 "$src" "$dest"
    else
        # Fallback: use --progress for older rsync versions
        rsync -a --delete --progress "$src" "$dest" 2>/dev/null || rsync -a --delete "$src" "$dest"
    fi
}
