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

# BSD stat's %T is a file-type marker ("/" for directories), not a
# filesystem type. Read the kernel mount table and choose the nearest
# containing mount after resolving existing symlinked path components.
_macos_mount_filesystem_type() {
    local probe="$1" candidate mounts line suffix prefix fs_type
    candidate="$(cd "$probe" && pwd -P)" || return 1
    mounts="$(LC_ALL=C mount 2>/dev/null)" || return 1
    while :; do
        while IFS= read -r line; do
            [[ "$line" == *" ("*")" ]] || continue
            suffix="${line##* (}"
            prefix="${line%" ($suffix"}"
            [[ "$prefix" == *" on $candidate" ]] || continue
            fs_type="${suffix%%,*}"
            fs_type="${fs_type%)}"
            [[ "$fs_type" =~ ^[A-Za-z0-9_]+$ ]] || continue
            printf '%s\n' "$fs_type"
            return 0
        done <<< "$mounts"
        [[ "$candidate" == / ]] && break
        candidate="${candidate%/*}"
        [[ -n "$candidate" ]] || candidate=/
    done
    return 1
}

# Detect the filesystem containing the nearest existing installation parent.
test_install_dir_filesystem() {
    local install_dir="${1:-$INSTALL_DIR}"
    INSTALL_FS_TYPE=""
    INSTALL_FS_FATAL=false

    local probe
    probe="$(_resolve_existing_parent "$install_dir")"

    local fs_type=""
    fs_type="$(_macos_mount_filesystem_type "$probe" || true)"

    # Retain diskutil as an optional fallback if the mount table is unavailable.
    # A subdirectory may not be accepted by diskutil; never treat an error as a
    # filesystem name or let an optional probe abort a strict-shell installer.
    if [[ -z "$fs_type" ]] && command -v diskutil >/dev/null 2>&1; then
        fs_type=$(LC_ALL=C diskutil info "$probe" 2>/dev/null \
            | awk -F': *' '/File System Personality/ {print $2; exit}' \
            | tr '[:upper:]' '[:lower:]' || true)
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
