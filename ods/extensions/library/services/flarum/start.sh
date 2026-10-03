#!/bin/bash
set -eu
[[ ${DB_PASSWORD:-} =~ ^[0-9a-fA-F]{64}$ ]] || { echo 'DB_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1; }
exec /init "$@"
