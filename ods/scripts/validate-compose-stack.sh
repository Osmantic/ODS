#!/usr/bin/env bash
# Validate resolved Docker Compose stack for syntax errors
# Usage: validate-compose-stack.sh --compose-flags "-f file1.yml -f file2.yml" [--env-file /path/to/.env]
# Quote or backslash-escape paths inside --compose-flags using POSIX shell syntax.
# Flags are parsed as arguments; shell substitutions and globbing are not performed.
#
# Returns:
#   0 - Valid compose stack
#   1 - Invalid compose stack (syntax errors, missing files, etc.)

set -euo pipefail

COMPOSE_FLAGS=""
ENV_FILE=""
QUIET=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --compose-flags)
            COMPOSE_FLAGS="${2:-}"
            shift 2
            ;;
        --env-file)
            ENV_FILE="${2:-}"
            shift 2
            ;;
        --quiet)
            QUIET=true
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$COMPOSE_FLAGS" ]]; then
    echo "ERROR: --compose-flags required" >&2
    exit 1
fi

# Parse the complete string before invoking Docker. A temporary NUL-delimited
# stream preserves empty arguments and newlines on Bash 3, and lets us check the
# parser's exit status (which process substitution would hide).
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required to parse --compose-flags" >&2
    exit 1
fi
validation_output=$(mktemp)
trap 'rm -f "$validation_output"' EXIT
if ! python3 -I - "$COMPOSE_FLAGS" > "$validation_output" <<'PY'
import os
import shlex
import sys

try:
    flags = shlex.split(sys.argv[1], comments=False, posix=True)
except ValueError as exc:
    print(f"ERROR: invalid --compose-flags: {exc}", file=sys.stderr)
    sys.exit(1)
if not flags:
    print("ERROR: --compose-flags requires at least one argument", file=sys.stderr)
    sys.exit(1)
for flag in flags:
    sys.stdout.buffer.write(os.fsencode(flag) + b"\0")
PY
then
    exit 1
fi
COMPOSE_FLAGS_ARR=()
while IFS= read -r -d '' compose_arg; do
    COMPOSE_FLAGS_ARR+=("$compose_arg")
done < "$validation_output"

# Check if docker/docker compose is available
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    DOCKER_COMPOSE_CMD=(docker compose)
elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE_CMD=(docker-compose)
else
    echo "ERROR: docker compose not found" >&2
    exit 1
fi

# Append only present env-file arguments, avoiding empty-array expansion under
# Bash 3's nounset behavior.
if [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
    DOCKER_COMPOSE_CMD+=(--env-file "$ENV_FILE")
fi

# Validate compose stack syntax
if ! $QUIET; then
    echo "Validating compose stack: $COMPOSE_FLAGS"
fi

# Use docker compose config to validate syntax and merge
# This catches:
# - YAML syntax errors
# - Missing files
# - Invalid service definitions
# - Circular dependencies
# - Invalid environment variable references
if "${DOCKER_COMPOSE_CMD[@]}" "${COMPOSE_FLAGS_ARR[@]}" config > "$validation_output" 2>&1; then
    if ! $QUIET; then
        echo "Compose stack validation passed"
        # Show summary of services
        service_count=$(grep -c "^  [a-z]" "$validation_output" || echo "0")
        echo "  Services defined: $service_count"
    fi
    exit 0
else
    echo "Compose stack validation FAILED" >&2
    echo "" >&2
    echo "Errors:" >&2
    cat "$validation_output" >&2
    echo "" >&2
    echo "Compose flags: $COMPOSE_FLAGS" >&2
    exit 1
fi
