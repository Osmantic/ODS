#!/bin/bash
# ============================================================================
# Test: community extensions port-binding sweep
# ============================================================================
# Regression guard for PR #964 follow-up: every community extension compose
# file under dream-server/extensions/library/services/ must bind its host
# ports via ${BIND_ADDRESS:-127.0.0.1} — never a bare "127.0.0.1:" literal.
# A hard-coded 127.0.0.1 defeats the --lan / dashboard opt-in that flips
# BIND_ADDRESS to 0.0.0.0.
#
# Scope: ports: list entries only. healthcheck: blocks reference 127.0.0.1
# as container-internal loopback and are excluded.
#
# Usage: bash tests/test-bind-address-sweep.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

EXT_DIR="$REPO_ROOT/dream-server/extensions/library/services"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [[ ! -d "$EXT_DIR" ]]; then
    echo -e "  ${RED}FAIL${NC} community extensions directory missing: $EXT_DIR"
    exit 1
fi

# Match lines that start a ports entry with a literal 127.0.0.1, quoted or not.
# Healthcheck URLs live on `test:` lines and never start with `- "127.0.0.1:`
# or `- 127.0.0.1:`, so this pattern is specific to ports: entries.
OFFENDERS="$(grep -REn '^\s*-\s*"?127\.0\.0\.1:' "$EXT_DIR" --include='compose.yaml' || true)"

if [[ -n "$OFFENDERS" ]]; then
    echo -e "  ${RED}FAIL${NC} community extensions still bind to literal 127.0.0.1 in ports:"
    echo "$OFFENDERS"
    echo ""
    echo "  Use the BIND_ADDRESS pattern instead, e.g.:"
    echo '    - "${BIND_ADDRESS:-127.0.0.1}:${EXT_PORT:-NNNN}:NNNN"'
    exit 1
fi

echo -e "  ${GREEN}PASS${NC} no literal 127.0.0.1 ports bindings in community extensions"
exit 0
