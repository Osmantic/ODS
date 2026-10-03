#!/usr/bin/env bash
# Prepare fixed WSL tmpfs directories for the two sockets consumed by Pixel
# Edge. The host services create the sockets there directly. No bind mount is
# needed: Docker Desktop can bind a stable directory before or after the host
# services start, and socket replacement remains visible through that inode.
set -euo pipefail

fail() {
    echo "ods-pixel-wsl-runtime-bridge: $*" >&2
    exit 1
}

# Count both former WSL targets and Docker Desktop's projections of those
# targets. A partial legacy cleanup could leave only the projections visible;
# allowing the new socket layout then would mix old and new socket routes.
# A projection's mountinfo root can name the old WSL target or its original
# /run source, depending on which bind Docker Desktop projected.
# Keep the input argument for a pure mountinfo fixture; the systemd path always
# reads the executing namespace's /proc/self/mountinfo.
ods_count_legacy_wsl_mounts() {
    local mountinfo="${1:-/proc/self/mountinfo}"
    awk '
        $5 == "/mnt/wsl/ods-portal-runtime/ingress" \
            || $5 == "/mnt/wsl/ods-portal-runtime/preview" { count++; next }
        ($4 == "/ods-portal-runtime/ingress" \
            || $4 == "/ods-portal-runtime/preview" \
            || $4 == "/ods-pixel" || $4 == "/ods-pixel-preview" \
            || $4 == "/run/ods-pixel" || $4 == "/run/ods-pixel-preview") \
            && $5 ~ /^\/mnt\/wsl\/docker-desktop-bind-mounts\// { count++ }
        END { print count + 0 }
    ' "$mountinfo"
}

# Tests source this file to exercise the exact parser without root or WSL.
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0

action="${1:-}"
[[ "$#" -ge 1 && "$#" -le 2 && ( "$action" == ensure || "$action" == remove ) ]] || exit 2
[[ "$(id -u)" -eq 0 ]] || fail "must run as root"
# ExecStop is deliberately a no-op. It must still succeed if WSL propagation
# changed after startup; removal never touches sockets, directories, or mounts.
[[ "$action" == ensure ]] || exit 0
grep -qi microsoft /proc/sys/kernel/osrelease || fail "not a WSL kernel"
[[ "$(findmnt -n -o PROPAGATION -T /mnt/wsl)" == shared ]] || fail "/mnt/wsl is not a shared mount"

# A retained install can still have the former forward binds on these exact
# legacy paths. They multiply in Docker Desktop's shared mount graph. Do not
# start new services in a namespace carrying that stack or mutate those mounts
# here; the retained-upgrade migration must retire them under an owner lock.
legacy_layers="$(ods_count_legacy_wsl_mounts)"
(( legacy_layers == 0 )) || fail "legacy Pixel runtime mounts remain ($legacy_layers); complete the guarded WSL mount migration before retrying"

owner="${2:-${PIXEL_SERVICE_USER:-}}"
[[ "$owner" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || fail "invalid Pixel service owner"
owner_uid="$(id -u "$owner" 2>/dev/null)" || fail "Pixel service owner does not exist"
[[ "$owner_uid" -ne 0 ]] || fail "Pixel service owner must not be root"
getent group ods-pixel >/dev/null || fail "ods-pixel group does not exist"

base=/mnt/wsl/ods-portal-sockets
wsl_device="$(stat -Lc '%d' /mnt/wsl)"
[[ ! -L "$base" ]] || fail "$base is a symlink"
[[ ! -e "$base" || -d "$base" ]] || fail "$base is not a directory"
! mountpoint -q -- "$base" || fail "$base is mounted"
# An existing directory may contain operator data. Refuse it before install -d
# can change its owner or mode, even if the path sits on the expected tmpfs.
if [[ -d "$base" ]]; then
    [[ "$(stat -Lc '%d' -- "$base")" == "$wsl_device" ]] || fail "$base is not on /mnt/wsl"
    [[ "$(stat -Lc '%u:%g:%a' -- "$base")" == 0:0:755 ]] \
        || fail "$base has unexpected ownership or mode"
    shopt -s nullglob dotglob
    for entry in "$base"/*; do
        [[ ( "${entry##*/}" == ingress || "${entry##*/}" == preview ) \
            && -d "$entry" && ! -L "$entry" ]] || fail "$base contains an unexpected entry"
    done
    shopt -u nullglob dotglob
fi
install -d -o root -g root -m 0755 -- "$base"
[[ "$(stat -Lc '%d' "$base")" == "$wsl_device" ]] || fail "$base is not on /mnt/wsl"

prepare() {
    local target="$1" mode="$2" expected_root="$3" allowed_name="$4"
    local mount_rows entry count bad
    [[ ! -L "$target" ]] || fail "$target is a symlink"
    [[ ! -e "$target" || -d "$target" ]] || fail "$target is not a directory"
    if [[ -d "$target" ]]; then
        [[ "$(stat -Lc '%d' -- "$target")" == "$wsl_device" ]] \
            || fail "$target still has the old bind mount; stop Pixel Edge and retire that mount before retrying"
        # This directory is writable by the service owner. Reuse it only when
        # its existing custody matches our contract; install -d would silently
        # chown/chmod an unrelated directory otherwise.
        [[ "$(stat -Lc '%u:%G:%a' -- "$target")" == "$owner_uid:ods-pixel:$mode" ]] \
            || fail "$target has unexpected ownership or mode"
    fi
    # Docker Desktop may leave a self-bind of this exact directory. Refuse an
    # unrelated same-device bind or a pathological stack before changing modes.
    mount_rows="$(awk -v path="$target" -v root="$expected_root" '
        $5 == path { count++; if ($4 != root) bad = 1 }
        END { print count + 0, bad + 0 }
    ' /proc/self/mountinfo)"
    read -r count bad <<<"$mount_rows"
    [[ "$bad" == 0 ]] || fail "$target is bound from an unexpected directory"
    (( count <= 8 )) || fail "$target has too many mount layers ($count); refusing to amplify them"
    if [[ -d "$target" ]]; then
        shopt -s nullglob dotglob
        for entry in "$target"/*; do
            [[ "${entry##*/}" == "$allowed_name" && -S "$entry" && ! -L "$entry" ]] \
                || fail "$target contains an unexpected entry"
            [[ "$(stat -c '%u' -- "$entry")" == "$owner_uid" ]] \
                || fail "$target contains a socket owned by another user"
        done
        shopt -u nullglob dotglob
    fi
    install -d -o "$owner" -g ods-pixel -m "$mode" -- "$target"
    [[ "$(stat -Lc '%d' -- "$target")" == "$wsl_device" ]] || fail "$target left /mnt/wsl"
    [[ "$(stat -Lc '%u:%G:%a' -- "$target")" == "$owner_uid:ods-pixel:$mode" ]] \
        || fail "$target has incorrect ownership or mode"
}

prepare "$base/ingress" 710 /ods-portal-sockets/ingress pixel-ingress.sock
prepare "$base/preview" 750 /ods-portal-sockets/preview http.sock
