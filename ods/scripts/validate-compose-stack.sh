#!/usr/bin/env bash
# Validate resolved Docker Compose stack for syntax errors
# Usage: validate-compose-stack.sh --compose-flags "-f file1.yml -f file2.yml" [--env-file /path/to/.env]
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

# Build env-file flag if provided (allows compose to resolve required variable references)
ENV_FILE_FLAG=""
if [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
    ENV_FILE_FLAG="--env-file $ENV_FILE"
fi

# Check if docker/docker compose is available
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo "ERROR: docker compose not found" >&2
    exit 1
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
validation_output=$(mktemp)
if $DOCKER_COMPOSE_CMD $ENV_FILE_FLAG $COMPOSE_FLAGS config > "$validation_output" 2>&1; then
    if ! $QUIET; then
        echo "Compose stack validation passed"
        # Show summary of services
        service_count=$(grep -c "^  [a-z]" "$validation_output" || echo "0")
        echo "  Services defined: $service_count"
    fi
    rm -f "$validation_output"
    exit 0
else
    echo "Compose stack validation FAILED" >&2
    echo "" >&2
    echo "Errors:" >&2
    cat "$validation_output" >&2
    echo "" >&2
    echo "Compose flags: $COMPOSE_FLAGS" >&2
    rm -f "$validation_output"
    exit 1
fi
