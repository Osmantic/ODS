#!/usr/bin/env bash
# Real mounts, confined to a disposable mount namespace; no Docker or services.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != --inside ]]; then
    [[ $EUID -eq 0 ]] || { echo 'Run this isolated mount test with sudo.' >&2; exit 1; }
    exec unshare --mount --propagation private bash "$0" --inside
fi
if mountpoint -q /run; then umount -R /run; fi
mount -t tmpfs tmpfs /run
mount --make-shared /run
# Mask /mnt rather than creating anything in the host's existing /mnt/wsl.
# Copy the scripts first because a Windows checkout may itself live in /mnt.
stage=$(mktemp -d /tmp/ods-wsl-bootstrap.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
cp "$root/installers/lib/pixel-host-install.sh" "$stage/installer.sh"
cp "$root/installers/lib/pixel-integration.sh" "$stage/pixel-integration.sh"
mkdir -p "$stage/plugin/host" "$stage/install"
cp "$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh" "$stage/plugin/host/"
if mountpoint -q /mnt/wsl; then umount -R /mnt/wsl; fi
mount -t tmpfs tmpfs /mnt
mkdir /mnt/wsl
mount -t tmpfs tmpfs /mnt/wsl
mount --make-shared /mnt/wsl
mount -t tmpfs tmpfs /usr/local
mkdir /usr/local/libexec
owner=nobody
group=$(id -gn "$owner")
fixture_uid=$(id -u "$owner")
fixture_gid=$(id -g "$owner")
chown "$fixture_uid:$fixture_gid" "$stage/plugin/host/pixel-wsl-runtime-bridge.sh"
chmod 0755 "$stage/plugin/host/pixel-wsl-runtime-bridge.sh"
# Portable fixture for the WSL-only kernel probe and the dedicated ODS group.
grep() {
    if [[ "$*" == '-qi microsoft /proc/sys/kernel/osrelease' ]]; then return 0; fi
    command grep "$@"
}
getent() {
    if [[ "$*" == 'group ods-pixel' ]]; then printf 'ods-pixel:x:%s:\n' "$fixture_gid"; return; fi
    command getent "$@"
}
ods_sudo() {
    local -a args=()
    local arg
    for arg in "$@"; do [[ "$arg" != ods-pixel ]] || arg="$group"; args+=("$arg"); done
    "${args[@]}"
}
export -f grep
source "$stage/installer.sh"
export INSTALL_DIR="$stage/install"
cat > "$INSTALL_DIR/.env" <<'ENV'
PIXEL_RUNTIME_BIND_PROPAGATION=rshared
PIXEL_INGRESS_RUNTIME_DIR=/mnt/wsl/ods-portal-runtime/ingress
PIXEL_PREVIEW_RUNTIME_DIR=/mnt/wsl/ods-portal-runtime/preview
ENV
_ods_pixel_prepare_wsl_bridge "$owner" "$stage/plugin"
ingress_inode=$(stat -c '%d:%i' /run/ods-pixel)
preview_inode=$(stat -c '%d:%i' /run/ods-pixel-preview)
[[ "$(stat -c '%d:%i' /mnt/wsl/ods-portal-runtime/ingress)" == "$ingress_inode" ]]
[[ "$(stat -c '%d:%i' /mnt/wsl/ods-portal-runtime/preview)" == "$preview_inode" ]]
echo ready > /run/ods-pixel/probe
[[ "$(cat /mnt/wsl/ods-portal-runtime/ingress/probe)" == ready ]]
_ods_pixel_prepare_wsl_bridge "$owner" "$stage/plugin"
/usr/local/libexec/ods-pixel-wsl-runtime-bridge ensure
[[ "$(stat -c '%u:%g:%a:%d:%i' /run/ods-pixel)" == "$fixture_uid:$fixture_gid:710:$ingress_inode" ]]
[[ "$(stat -c '%u:%g:%a:%d:%i' /run/ods-pixel-preview)" == "$fixture_uid:$fixture_gid:750:$preview_inode" ]]
echo 'PASS clean bootstrap creates shared mounts; repeat/later ensure preserves ownership and inode'
sed -i 's@/mnt/wsl/ods-portal-runtime/ingress@/unrelated@' "$INSTALL_DIR/.env"
if _ods_pixel_prepare_wsl_bridge "$owner" "$stage/plugin"; then echo 'FAIL accepted unrelated target'; exit 1; fi
sed -i 's@/unrelated@/mnt/wsl/ods-portal-runtime/ingress@' "$INSTALL_DIR/.env"
chmod 0777 /run/ods-pixel
if _ods_pixel_prepare_wsl_bridge "$owner" "$stage/plugin"; then echo 'FAIL accepted unsafe runtime mode'; exit 1; fi
chmod 0710 /run/ods-pixel
umount /mnt/wsl/ods-portal-runtime/ingress
mount -t tmpfs tmpfs /mnt/wsl/ods-portal-runtime/ingress
if /usr/local/libexec/ods-pixel-wsl-runtime-bridge ensure; then echo 'FAIL accepted unrelated mounted inode'; exit 1; fi
[[ "$(stat -c '%u:%g:%a:%d:%i' /run/ods-pixel)" == "$fixture_uid:$fixture_gid:710:$ingress_inode" ]]
echo 'PASS altered route, unsafe permissions and mismatched mounts are rejected without changing source'
