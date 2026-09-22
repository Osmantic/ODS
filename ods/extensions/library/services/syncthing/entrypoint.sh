#!/bin/sh
# The pinned Alpine image supplies ash, including pipefail.
# shellcheck disable=SC3040
set -euo pipefail

umask 077
mkdir -p /var/syncthing/files
if [ ! -d /var/syncthing/config ]; then
    : "${SYNCTHING_ADMIN_USER:?Set SYNCTHING_ADMIN_USER}"
    : "${SYNCTHING_ADMIN_PASSWORD:?Set SYNCTHING_ADMIN_PASSWORD}"
    # Upstream's stdin password reader consumes one line. Reject line breaks
    # instead of silently hashing only the first part of a supplied password.
    case "$SYNCTHING_ADMIN_PASSWORD" in
        *"
"*|*"$(printf '\r')"*)
            printf '%s\n' 'SYNCTHING_ADMIN_PASSWORD must be a single line' >&2
            exit 1
            ;;
    esac
    # Generate keys and hash the password before any listener exists. Publish
    # the complete directory only after success, so interrupted initialization
    # cannot leave an unauthenticated config for the next start.
    initial=$(mktemp -d /var/syncthing/.ods-initialize-XXXXXX)
    trap 'rm -rf "$initial"' EXIT
    cp /ods/config.xml "$initial/config.xml"
    printf '%s\n' "$SYNCTHING_ADMIN_PASSWORD" |
        syncthing generate --home="$initial" --no-port-probing \
            --gui-user="$SYNCTHING_ADMIN_USER" --gui-password=-
    mv "$initial" /var/syncthing/config
    trap - EXIT
fi
if [ ! -s /var/syncthing/config/config.xml ]; then
    printf '%s\n' 'Existing Syncthing state has no complete config.xml; restore a stopped backup' >&2
    exit 1
fi
unset SYNCTHING_ADMIN_PASSWORD
exec syncthing serve --no-browser --no-restart --no-upgrade
