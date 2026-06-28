#!/bin/bash
# ============================================================================
# ODS macOS Installer -- Filesystem & Docker Desktop Sharing Checks
# ============================================================================
# Part of: installers/macos/lib/
# Purpose: Detect non-POSIX filesystems at the install path (which silently
#          drop chmod/chown — leaking .env secrets) and Docker Desktop file
#          sharing allowlist gaps (which surface as cryptic OCI mount errors).
#
# Provides:
#   test_install_dir_filesystem() -- sets INSTALL_FS_TYPE, INSTALL_FS_FATAL,
#                                    INSTALL_FS_NETWORKED
#   test_docker_desktop_sharing() -- sets DOCKER_SHARE_OK, DOCKER_SHARE_ERR
#
# shellcheck disable=SC2034  # vars are read by install-macos.sh after sourcing
#
# Modder notes:
#   POSIX-permission filesystems leave chmod 600 .env useful. Non-POSIX
#   filesystems (exFAT/FAT/NTFS-via-fuseblk) make the chmod a no-op, so the
#   secrets file ends up world-readable — treat that as fatal.
#   Filesystems that hold containerised data subpaths only are warn-only.
# ============================================================================

# Resolve to the nearest existing path so `stat -f` doesn't fail when the
# install dir hasn't been created yet (first install).
_resolve_existing_parent() {
    local p="$1"
    while [[ -n "$p" && ! -e "$p" ]]; do
        p="$(dirname "$p")"
    done
    [[ -z "$p" ]] && p="/"
    printf '%s' "$p"
}

# Detect filesystem type at INSTALL_DIR's parent (where .env will live).
# BSD `stat -f %T` returns short personality strings (apfs, hfs, msdos, exfat,
# ntfs). diskutil gives a richer "File System Personality" line we fall back
# to when stat returns something unexpected.
test_install_dir_filesystem() {
    local install_dir="${1:-$INSTALL_DIR}"
    INSTALL_FS_TYPE=""
    INSTALL_FS_FATAL=false

    local probe
    probe="$(_resolve_existing_parent "$install_dir")"

    local fs_type=""
    fs_type=$(stat -f %T "$probe" 2>/dev/null || true)

    # `stat -f %T` on macOS sometimes returns just a short tag; cross-check
    # with diskutil for stable personality identification.
    if command -v diskutil >/dev/null 2>&1; then
        # `diskutil info` only accepts mount points or device IDs. Subpaths
        # (e.g. /Volumes/X/ods) make it exit non-zero, which under
        # `set -euo pipefail` would silently kill the entire installer. The
        # personality lookup is an optional refinement on top of `stat -f %T`,
        # so swallow non-zero — empty personality just means we keep the stat
        # value.
        local personality=""
        personality=$(diskutil info "$probe" 2>/dev/null \
            | awk -F': *' '/File System Personality/ {print $2; exit}' \
            | tr '[:upper:]' '[:lower:]' || true)
        if [[ -n "$personality" ]]; then
            fs_type="$personality"
        fi
    fi

    INSTALL_FS_TYPE="${fs_type:-unknown}"

    case "$INSTALL_FS_TYPE" in
        *exfat*|*msdos*|*fat32*|*fat16*|*"ms-dos"*|*ntfs*)
            INSTALL_FS_FATAL=true
            ;;
    esac

    INSTALL_FS_NETWORKED=false
    case "$INSTALL_FS_TYPE" in
        nfs|smbfs|afpfs|webdav)
            INSTALL_FS_NETWORKED=true
            ;;
    esac
}

# Smoke-test Docker Desktop's file-sharing allowlist by trying to bind-mount
# the install dir into a throwaway alpine container. Docker Desktop responds
# with a recognisable error when the path is not in the shared list.
test_docker_desktop_sharing() {
    local install_dir="${1:-$INSTALL_DIR}"
    DOCKER_SHARE_OK=true
    DOCKER_SHARE_ERR=""

    if ! command -v docker >/dev/null 2>&1; then
        DOCKER_SHARE_OK=false
        DOCKER_SHARE_ERR="docker CLI not found"
        return
    fi

    local probe
    probe="$(_resolve_existing_parent "$install_dir")"

    local out=""
    out=$(docker run --rm -v "${probe}:/check:ro" alpine true 2>&1) || true

    if echo "$out" | grep -qiE "not shared from the host|Mounts denied|file sharing|filesharing"; then
        DOCKER_SHARE_OK=false
        DOCKER_SHARE_ERR="$out"
    fi
}
