#!/bin/sh
# The pinned Alpine image supplies BusyBox ash with pipefail support.
set -euo pipefail
umask 077
validate_hash() {
    if [ "${#1}" -ne 60 ] || ! printf '%s\n' "$1" | grep -Eq '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'; then
        printf '%s\n' 'Apprise gateway requires a single bcrypt hash per account' >&2
        exit 1
    fi
}
validate_hash "$APPRISE_AUTH_HASH"
printf 'ods:%s\n' "$APPRISE_AUTH_HASH" > /tmp/passwords
exec nginx -g 'daemon off;'
