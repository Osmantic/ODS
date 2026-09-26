#!/bin/bash
# ============================================================================
# Test: community extensions port-binding sweep
# ============================================================================
# Regression guard for PR #964 follow-up: every community extension compose
# file under ods/extensions/library/services/ must bind its host
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

EXT_DIR="$REPO_ROOT/ods/extensions/library/services"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [[ ! -d "$EXT_DIR" ]]; then
    echo -e "  ${RED}FAIL${NC} community extensions directory missing: $EXT_DIR"
    exit 1
fi

# Follow the ports block, including YAML indentless sequence entries. A
# multiline healthcheck can also contain a bare loopback address; it is not a
# published host port and must remain container-internal.
OFFENDERS="$(find "$EXT_DIR" -type f -name compose.yaml -exec awk '
    FNR == 1 { in_ports = 0 }
    /^[[:space:]]*ports:[[:space:]]*(#.*)?$/ {
        match($0, /^[[:space:]]*/)
        ports_indent = RLENGTH
        in_ports = 1
        next
    }
    in_ports {
        match($0, /^[[:space:]]*/)
        indent = RLENGTH
        if ($0 !~ /^[[:space:]]*(#.*)?$/ &&
            (indent < ports_indent ||
             (indent == ports_indent && $0 !~ /^[[:space:]]*-/))) {
            in_ports = 0
        }
        if (in_ports && $0 ~ /^[[:space:]]*-[[:space:]]*[\042\047]?127[.]0[.]0[.]1:/) {
            print FILENAME ":" FNR ":" $0
        }
    }
' {} +)"

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
