#!/usr/bin/env bash
set -euo pipefail
umask 077
for key in NIFI_ADMIN_PASSWORD NIFI_SENSITIVE_PROPS_KEY; do
    if [[ ! ${!key:-} =~ ^[a-f0-9]{64}$ ]]; then
        echo "$key must contain 64 lowercase hexadecimal characters." >&2
        exit 1
    fi
done
if [[ "$NIFI_ADMIN_PASSWORD" == "$NIFI_SENSITIVE_PROPS_KEY" ]]; then
    echo 'Use distinct administrator and flow-encryption secrets.' >&2
    exit 1
fi
properties=/opt/nifi/nifi-current/conf/nifi.properties
existing=$(sed -n 's/^nifi.sensitive.props.key=//p' "$properties")
if [[ -n "$existing" && "$existing" != "$NIFI_SENSITIVE_PROPS_KEY" ]]; then
    echo 'Saved flow encryption key differs; preserve it or perform a native key migration.' >&2
    exit 1
fi
export SINGLE_USER_CREDENTIALS_USERNAME=ods
export SINGLE_USER_CREDENTIALS_PASSWORD="$NIFI_ADMIN_PASSWORD"
unset NIFI_ADMIN_PASSWORD
exec /opt/nifi/scripts/start.sh
