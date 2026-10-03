#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$root/scripts/ods-doctor.sh"

extract_function() {
    awk -v signature="^$1[(][)]" '
        $0 ~ signature { in_block = 1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$source_file"
}

eval "$(extract_function _doctor_disk_free_gb)"
eval "$(extract_function _doctor_external_inference_enabled)"
eval "$(extract_function _doctor_select_disk)"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

ROOT_DIR=/fixture/ods
HOME=/fixture/home
df() {
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    case "${*: -1}" in
        /fixture/home) printf '/dev/root 19922944 6291456 13631488 32%% /\n' ;;
        /var/lib/docker) printf '/dev/vdb1 102760448 46137344 56623104 45%% /var/lib/docker\n' ;;
        *) return 1 ;;
    esac
}
docker() {
    [[ "$1" == info && "$2" == --format ]] || return 2
    printf '/var/lib/docker\n'
}

EXTERNAL_LLM_URL=http://host.example:8080
LEMONADE_EXTERNAL=false
ODS_MODE=local
_doctor_select_disk
[[ "$DOCTOR_HOME_DISK_GB" == 13 && "$DISK_GB" == 54 \
    && "$DOCTOR_DISK_SOURCE" == /var/lib/docker ]] \
    || fail 'external inference must check the observable Docker data-root filesystem'

EXTERNAL_LLM_URL=
_doctor_select_disk
[[ "$DISK_GB" == 13 && "$DOCTOR_DISK_SOURCE" == /fixture/home ]] \
    || fail 'managed local inference must retain the HOME filesystem check'

EXTERNAL_LLM_URL=http://host.example:8080
docker() { return 1; }
_doctor_select_disk
[[ "$DISK_GB" == 13 && "$DOCTOR_DISK_SOURCE" == /fixture/home ]] \
    || fail 'an unobservable Docker data-root must fail closed to the HOME filesystem'

printf '[OK] doctor selects the storage filesystem used by external inference installs\n'
