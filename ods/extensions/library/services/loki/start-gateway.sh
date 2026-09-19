#!/bin/ash
# shellcheck shell=busybox
# The pinned Alpine image supplies BusyBox ash with pipefail support.
set -euo pipefail
umask 077
validate_hash() {
    if [ "${#1}" -ne 60 ] || ! printf '%s\n' "$1" | grep -Eq '^[$]2[aby][$][0-9]{2}[$][./A-Za-z0-9]{53}$'; then
        printf '%s\n' 'Loki gateway requires a single bcrypt hash per account' >&2
        exit 1
    fi
}
validate_hash "$LOKI_WRITER_HASH"
validate_hash "$LOKI_READER_HASH"
printf 'writer:%s\n' "$LOKI_WRITER_HASH" > /tmp/writers
printf 'reader:%s\n' "$LOKI_READER_HASH" > /tmp/readers
exec nginx -g 'daemon off;'
