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

bridge() {
    local source="$1" target="$2" source_inode target_inode
    [[ -d "$source" && ! -L "$source" ]] || fail "$source is missing or not a real directory"
    [[ ! -L "$target" ]] || fail "$target is a symlink"
    source_inode="$(stat -Lc '%d:%i' -- "$source")"
    if mountpoint -q -- "$target"; then
        target_inode="$(stat -Lc '%d:%i' -- "$target")"
        [[ "$source_inode" == "$target_inode" ]] \
            || fail "$target is mounted from another directory than $source ($target_inode, expected $source_inode)"
        if [[ "$action" == remove ]]; then
            umount -- "$target"
        fi
        return
    fi
    [[ "$action" == remove ]] && return 0
    [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "$target is not empty"
    mount --bind -- "$source" "$target"
    [[ "$(stat -Lc '%d:%i' -- "$target")" == "$source_inode" ]] || fail "$target does not show $source after binding"
    [[ "$(findmnt -n -o PROPAGATION -T "$target")" == shared ]] || fail "$target bind is not shared"
}

base=/mnt/wsl/ods-portal-runtime
[[ ! -L "$base" ]] || fail "$base is a symlink"
if [[ "$action" == ensure ]]; then
    install -d -o root -g root -m 0755 -- "$base" "$base/ingress" "$base/preview"
fi
bridge /run/ods-pixel "$base/ingress"
bridge /run/ods-pixel-preview "$base/preview"
