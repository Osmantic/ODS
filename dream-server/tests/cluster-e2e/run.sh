#!/usr/bin/env bash
# Build, run, collect exit code, tear down. Usage:
#   ./run.sh                 # full suite
#   ./run.sh test_discovery  # subset (passed through to pytest)
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.test.yml"

cleanup() {
    $COMPOSE down --remove-orphans --volumes 2>/dev/null || true
}
trap cleanup EXIT

$COMPOSE build
$COMPOSE up -d controller worker1 worker2
$COMPOSE run --rm test-runner "$@"
rc=$?

cleanup
trap - EXIT
exit $rc
