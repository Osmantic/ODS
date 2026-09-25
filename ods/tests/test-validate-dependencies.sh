#!/usr/bin/env bash
# lib/validate-dependencies.sh — service dependency validation contracts.
#
# The core services in docker-compose.base.yml have no extension manifest, so
# validate_service_dependencies() has to learn them by reading that file. It
# must read only the keys under `services:` — anchors (`x-logging`), `volumes`
# and `networks` entries are not services, and treating them as enabled makes
# the validator accept a depends_on it should reject.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASSED=0
FAILED=0

pass() {
    printf '[PASS] %s\n' "$1"
    PASSED=$((PASSED + 1))
}

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    FAILED=$((FAILED + 1))
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# A base compose file shaped like the real one: a YAML anchor above
# `services:`, and a `networks:` section below it.
cat > "$WORK_DIR/docker-compose.base.yml" <<'YAML'
x-logging: &default-logging
  driver: "json-file"
  options:
    max-size: "10m"

services:
  llama-server:
    image: example/llama
  open-webui:
    image: example/webui

networks:
  default:
    name: ods-net

volumes:
  qdrant-data:
YAML

# Run one validation case in a subshell so the associative arrays and the
# sourced library never leak between cases.
#
# Usage: run_case <service_id> <space-separated-depends-on>
run_case() {
    local sid="$1" deps="$2"
    (
        set +e
        # shellcheck source=/dev/null
        source "$ROOT_DIR/lib/validate-dependencies.sh"

        declare -A SERVICE_COMPOSE SERVICE_DEPENDS
        declare -a SERVICE_IDS

        SERVICE_IDS=("$sid")
        SERVICE_COMPOSE[$sid]="$WORK_DIR/enabled-compose.yaml"
        SERVICE_DEPENDS[$sid]="$deps"
        : > "${SERVICE_COMPOSE[$sid]}"

        INSTALL_DIR="$WORK_DIR"
        validate_service_dependencies >/dev/null 2>&1
        echo "$?"
    )
}

expect_rc() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        pass "$label"
    else
        fail "$label (expected rc $expected, got $actual)"
    fi
}

expect_rc "accepts a dependency on a base-compose service" \
    0 "$(run_case my-ext "llama-server")"

expect_rc "accepts a dependency on a second base-compose service" \
    0 "$(run_case my-ext "open-webui")"

expect_rc "rejects a dependency on a service nothing provides" \
    1 "$(run_case my-ext "nonexistent-service")"

# Regression: these are YAML keys inside `x-logging`, `networks` and `volumes`,
# not services. A validator that scans every two-space-indented key in the file
# accepts them and lets a genuinely unmet dependency through.
expect_rc "rejects a dependency named after a logging-anchor key" \
    1 "$(run_case my-ext "driver")"

expect_rc "rejects a dependency named after a logging-anchor option key" \
    1 "$(run_case my-ext "options")"

expect_rc "rejects a dependency named after a network" \
    1 "$(run_case my-ext "default")"

expect_rc "rejects a dependency named after a volume" \
    1 "$(run_case my-ext "qdrant-data")"

expect_rc "rejects a mixed list when one dependency is unmet" \
    1 "$(run_case my-ext "llama-server default")"

printf '\n%d passed, %d failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
