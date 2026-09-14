#!/usr/bin/env bash
set -euo pipefail

# The upstream entrypoint prints malformed NEO4J_AUTH verbatim. Reject unsupported
# initial-password syntax before constructing that value.
password="${ODS_NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"
if (( ${#password} < 12 )) || [[ "$password" == *'/'* || "$password" == *$'\n'* || "$password" == *$'\r'* ]]; then
    printf '%s\n' 'NEO4J_PASSWORD must have at least 12 characters and contain no slash or line break.' >&2
    exit 1
fi
export NEO4J_AUTH="neo4j/$password"
unset ODS_NEO4J_PASSWORD password
exec /startup/docker-entrypoint.sh "$@"
