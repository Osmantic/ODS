#!/bin/bash
set -eu
[[ ${ODS_NEO4J_PASSWORD:-} =~ ^[0-9a-fA-F]{64}$ ]] || { echo 'NEO4J_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1; }
export NEO4J_AUTH="neo4j/$ODS_NEO4J_PASSWORD"
unset ODS_NEO4J_PASSWORD
exec /startup/docker-entrypoint.sh "$@"
