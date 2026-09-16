#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE11="$ROOT_DIR/installers/phases/11-services.sh"

grep -q '_phase11_recovery_compose_ok=false' "$PHASE11"
grep -q 'if \$DOCKER_COMPOSE_CMD .*up -d --remove-orphans --no-build --pull never' "$PHASE11"
grep -q '_phase11_recovery_compose_ok=true' "$PHASE11"
grep -q 'if ! \$compose_ok && \$_phase11_recovery_compose_ok' "$PHASE11"

echo '[PASS] Phase 11 recovery Compose status is propagated'
