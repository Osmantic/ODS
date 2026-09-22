#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ ! ${ORTHANC_PASSWORD:-} =~ ^[a-f0-9]{64}$ ]]; then
    echo 'ORTHANC_PASSWORD must contain 64 lowercase hexadecimal characters.' >&2
    exit 1
fi
exec /docker-entrypoint.sh "$@"
