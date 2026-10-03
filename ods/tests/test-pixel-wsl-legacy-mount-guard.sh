#!/usr/bin/env bash
set -euo pipefail

ods_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Sourcing exposes only the production mountinfo parser; it does not run the
# privileged bridge or change the test host's mounts.
source "$ods_dir/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh"

fixture="$(mktemp)"
trap 'rm -f -- "$fixture"' EXIT

old_ingress='101 10 0:32 /ods-portal-runtime/ingress /mnt/wsl/ods-portal-runtime/ingress rw,relatime shared:1 - tmpfs none rw'
old_preview='102 10 0:32 /ods-portal-runtime/preview /mnt/wsl/ods-portal-runtime/preview rw,relatime shared:1 - tmpfs none rw'
proxy_ingress='103 10 0:32 /ods-portal-runtime/ingress /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash1 rw,relatime shared:1 - tmpfs none rw'
proxy_preview='104 10 0:32 /ods-portal-runtime/preview /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash2 rw,relatime shared:1 - tmpfs none rw'
proxy_runtime_ingress='108 10 0:31 /ods-pixel /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash5 rw,relatime shared:1 - tmpfs none rw'
proxy_runtime_preview='109 10 0:31 /ods-pixel-preview /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash6 rw,relatime shared:1 - tmpfs none rw'
proxy_rootfs_ingress='110 10 8:1 /run/ods-pixel /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash7 rw,relatime shared:1 - ext4 root rw'
proxy_rootfs_preview='111 10 8:1 /run/ods-pixel-preview /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash8 rw,relatime shared:1 - ext4 root rw'
unrelated='105 10 0:32 /another-service/ingress /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash3 rw,relatime shared:1 - tmpfs none rw'
new_direct='106 10 0:32 /ods-portal-sockets/ingress /mnt/wsl/ods-portal-sockets/ingress rw,relatime shared:1 - tmpfs none rw'
wrong_prefix='107 10 0:32 /ods-portal-runtime/ingress /mnt/wsl/docker-desktop-bind-mounts-old/Ubuntu-24.04/hash4 rw,relatime shared:1 - tmpfs none rw'
similar_root='112 10 0:31 /ods-pixel-other /mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/hash9 rw,relatime shared:1 - tmpfs none rw'

assert_count() {
    local expected="$1" actual
    shift
    printf '%s\n' "$@" > "$fixture"
    actual="$(ods_count_legacy_wsl_mounts "$fixture")"
    [[ "$actual" == "$expected" ]] || {
        printf 'legacy mount count: expected %s, got %s\n' "$expected" "$actual" >&2
        exit 1
    }
}

assert_count 0 "$unrelated" "$new_direct" "$wrong_prefix" "$similar_root"
assert_count 2 "$old_ingress" "$old_preview"
# A partial cleanup may leave only Docker Desktop projections. This state is
# a precautionary fixture, not a claim that it was observed on a live host.
assert_count 2 "$proxy_ingress" "$proxy_preview"
assert_count 2 "$proxy_runtime_ingress" "$proxy_runtime_preview"
assert_count 2 "$proxy_rootfs_ingress" "$proxy_rootfs_preview"
assert_count 4 "$old_ingress" "$old_preview" "$proxy_ingress" "$proxy_preview"
assert_count 4 "$old_ingress" "$old_preview" "$proxy_runtime_ingress" "$proxy_runtime_preview"
assert_count 2 "$proxy_ingress" "$unrelated" "$proxy_preview" "$new_direct"
printf 'Pixel WSL legacy mount guard: passed\n'
