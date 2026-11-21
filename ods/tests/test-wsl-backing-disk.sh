#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/installers/lib/wsl-disk.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ods_is_wsl_host() { [[ "$is_wsl" == true ]]; }
log() { messages+="$*"; }
warn() { warnings+="$*"; }
stat() {
    [[ "$1" == -Lc && "$2" == '%d' ]] || return 1
    case "$3" in
        /) echo 1 ;;
        /install|"$TMP") echo "$install_dev" ;;
        "$drive_mount") [[ "$drive_dev" != missing ]] && echo "$drive_dev" ;;
        *) return 1 ;;
    esac
}
wslpath() {
    [[ "$1" == -u && "$2" == "${ODS_WSL_DISK_DRIVE}\\" ]] || return 1
    [[ "$drive_mount" != missing ]] || return 1
    printf '%s\n' "$drive_mount"
}
df() {
    [[ "$1" == -Pk ]] || return 1
    local available="$free_kib"
    if [[ "$2" == "$TMP" ]]; then available=868220928;
    elif [[ "$2" != "$drive_mount" ]]; then return 1; fi
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    printf 'disk 999999999 1 %s 1%% %s\n' "$available" "$2"
}
reset() {
    is_wsl=true install_dev=1 drive_dev=2 drive_mount='/custom mount/c'
    ODS_WSL_DISK_DRIVE=C: DISK_AVAIL=828 free_kib=4194304 messages='' warnings=''
}
check() { [[ "$DISK_AVAIL" == "$1" ]] || { echo "Expected $1, got $DISK_AVAIL" >&2; exit 1; }; }

reset; ods_bound_wsl_disk_avail /install; check 4
[[ -z "$warnings" && "$messages" == *'Windows drive C:'* ]]
reset; DISK_AVAIL=3; ods_bound_wsl_disk_avail /install; check 3
reset; ODS_WSL_DISK_DRIVE=d:; drive_mount='/custom mount/d'; free_kib=9437184
ods_bound_wsl_disk_avail /install; check 9
reset; install_dev=3; ods_bound_wsl_disk_avail /install; check 828
[[ -z "$warnings$messages" ]]
reset; install_dev=2; ods_bound_wsl_disk_avail /install; check 828
reset; is_wsl=false; ods_bound_wsl_disk_avail /install; check 828
[[ -z "$warnings$messages" ]]
reset; ODS_WSL_DISK_DRIVE='C:bad'; ods_bound_wsl_disk_avail /install; check 828
[[ "$warnings" == *'could not be checked'* ]]
reset; ODS_WSL_DISK_DRIVE=''; ods_bound_wsl_disk_avail /install; check 828
[[ "$warnings" == *'could not be checked'* ]]
reset; drive_mount=missing; ods_bound_wsl_disk_avail /install; check 828
[[ "$warnings" == *'not mounted'* ]]
reset; drive_dev=1; ods_bound_wsl_disk_avail /install; check 828
[[ "$warnings" == *'not mounted'* ]]
reset; free_kib=invalid; ods_bound_wsl_disk_avail /install; check 828
[[ "$warnings" == *'could not be read'* ]]
reset; free_kib=0; ods_bound_wsl_disk_avail /install; check 0
reset; INSTALL_DIR="$TMP/new-install"
# Run the installer's actual disk-detection block, including its ancestor
# walk and helper call, without invoking GPU detection or installation.
. <(sed -n '/^# Disk Detection$/,/^# GPU Detection$/p' "$ROOT/installers/phases/02-detection.sh")
check 4
echo 'PASS: WSL backing disk bounds, external filesystems, custom mounts, and unavailable probes'
