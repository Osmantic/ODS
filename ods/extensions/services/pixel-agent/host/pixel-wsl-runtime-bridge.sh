#!/usr/bin/env bash
# Docker Desktop and the owner's WSL distro have different /run filesystems.
# Bind the two fixed Pixel runtime directories into WSL's shared tmpfs so the
# edge can reach their sockets without exposing any broader host directory.
set -euo pipefail

# Every refusal names its reason: systemd records stderr in the journal, and a
# bare exit status there cannot be diagnosed on a user's machine.
fail() {
    echo "ods-pixel-wsl-runtime-bridge: $*" >&2
    exit 1
}

action="${1:-}"
[[ "$#" -eq 1 && "$action" == ensure || "$#" -eq 1 && "$action" == remove ]] || exit 2
[[ "$(id -u)" -eq 0 ]] || fail "must run as root"
grep -qi microsoft /proc/sys/kernel/osrelease || fail "not a WSL kernel"
[[ "$(findmnt -n -o PROPAGATION -T /mnt/wsl)" == shared ]] || fail "/mnt/wsl is not a shared mount"
wsl_device="$(stat -Lc '%d' /mnt/wsl)"

bridge() {
    local source="$1" target="$2" source_inode target_inode
    [[ -d "$source" && ! -L "$source" ]] || fail "$source is missing or not a real directory"
    [[ ! -L "$target" ]] || fail "$target is a symlink"
    source_inode="$(stat -Lc '%d:%i' -- "$source")"
    target_inode="$(stat -Lc '%d:%i' -- "$target")"
    if [[ "$target_inode" == "$source_inode" ]]; then
        if [[ "$action" == remove ]]; then
            umount -- "$target"
        fi
        return
    fi
    # systemd recreates a runtime directory when its service stops, so a bind
    # of the previous directory can remain here. It is ours: drop it.
    if mountpoint -q -- "$target" && [[ "${target_inode%%:*}" == "${source_inode%%:*}" ]]; then
        umount -- "$target"
        target_inode="$(stat -Lc '%d:%i' -- "$target")"
    fi
    [[ "$action" == remove ]] && return 0
    # The target may be a plain directory or Docker Desktop's own bind of it
    # (its WSL proxy mounts a bind source onto itself). Both live on the
    # shared /mnt/wsl tmpfs; bind the runtime on top, never over anything else.
    [[ "${target_inode%%:*}" == "$wsl_device" ]] \
        || fail "$target is mounted from another directory than $source ($target_inode, expected $source_inode or /mnt/wsl)"
    [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "$target is not empty"
    mount --bind -- "$source" "$target"
    [[ "$(stat -Lc '%d:%i' -- "$target")" == "$source_inode" ]] || fail "$target does not show $source after binding"
    # Stacked on Docker's own bind, findmnt lists every mount here; ours is last.
    [[ "$(findmnt -n -o PROPAGATION -T "$target" | tail -n 1)" == shared ]] || fail "$target bind is not shared"
}

base=/mnt/wsl/ods-portal-runtime
[[ ! -L "$base" ]] || fail "$base is a symlink"
if [[ "$action" == ensure ]]; then
    install -d -o root -g root -m 0755 -- "$base" "$base/ingress" "$base/preview"
fi
bridge /run/ods-pixel "$base/ingress"
bridge /run/ods-pixel-preview "$base/preview"
