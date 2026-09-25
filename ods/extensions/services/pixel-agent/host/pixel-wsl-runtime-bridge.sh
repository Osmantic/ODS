#!/usr/bin/env bash
# Docker Desktop and the owner's WSL distro have different /run filesystems.
# Bind the two fixed Pixel runtime directories into WSL's shared tmpfs so the
# edge can reach their sockets without exposing any broader host directory.
set -euo pipefail

action="${1:-}"
[[ "$#" -eq 1 && "$action" == ensure || "$#" -eq 1 && "$action" == remove ]] || exit 2
[[ "$(id -u)" -eq 0 ]] || exit 1
[[ -r /proc/sys/kernel/osrelease ]] && grep -qi microsoft /proc/sys/kernel/osrelease || exit 1
[[ "$(findmnt -n -o PROPAGATION -T /mnt/wsl)" == shared ]] || exit 1

bridge() {
    local source="$1" target="$2" source_inode target_inode
    [[ -d "$source" && ! -L "$source" ]] || return 1
    [[ ! -L "$target" ]] || return 1
    source_inode="$(stat -Lc '%d:%i' -- "$source")" || return 1
    if mountpoint -q -- "$target"; then
        target_inode="$(stat -Lc '%d:%i' -- "$target")" || return 1
        [[ "$source_inode" == "$target_inode" ]] || return 1
        if [[ "$action" == remove ]]; then
            umount -- "$target"
        fi
        return
    fi
    [[ "$action" == remove ]] && return 0
    [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] || return 1
    mount --bind -- "$source" "$target"
    [[ "$(stat -Lc '%d:%i' -- "$target")" == "$source_inode" ]] || return 1
    [[ "$(findmnt -n -o PROPAGATION -T "$target")" == shared ]] || return 1
}

base=/mnt/wsl/ods-portal-runtime
[[ ! -L "$base" ]] || exit 1
if [[ "$action" == ensure ]]; then
    install -d -o root -g root -m 0755 -- "$base" "$base/ingress" "$base/preview"
fi
bridge /run/ods-pixel "$base/ingress"
bridge /run/ods-pixel-preview "$base/preview"
