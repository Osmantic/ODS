#!/usr/bin/env bash
# The install summary must not print bare `docker compose` commands: the
# installed stack has no docker-compose.yml, so they fail with
# "no configuration file provided".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUMMARY="$ROOT/installers/phases/13-summary.sh"

for f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
    [[ ! -f "$ROOT/$f" ]] || { echo "FAIL: $f now ships; revisit this test"; exit 1; }
done
if grep -nE '^[[:space:]]*echo[[:space:]]+"[[:space:]]*docker compose (ps|logs|restart|up|down)' "$SUMMARY"; then
    echo "FAIL: summary prints bare docker compose commands that need a compose file"
    exit 1
fi
for cmd in "ods list" "ods logs" "ods restart"; do
    grep -q "echo \"  $cmd" "$SUMMARY" || { echo "FAIL: summary missing '$cmd'"; exit 1; }
done
echo "PASS: summary quick commands use the ods CLI"
