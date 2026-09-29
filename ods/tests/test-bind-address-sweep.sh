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

EXT_DIR="${ODS_BIND_ADDRESS_EXT_DIR:-$REPO_ROOT/ods/extensions/library/services}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
TEST_FIXTURES="$(mktemp -d)"
trap 'rm -rf "$TEST_FIXTURES"' EXIT

if [[ ! -d "$EXT_DIR" ]]; then
    echo -e "  ${RED}FAIL${NC} community extensions directory missing: $EXT_DIR"
    exit 1
fi

find_loopback_port_bindings() {
    local compose_dir="$1" file
    while IFS= read -r -d '' file; do
        awk -v source="$file" '
            /^[[:space:]]*ports:[[:space:]]*(#.*)?$/ {
                match($0, /^[[:space:]]*/)
                ports_indent = RLENGTH
                in_ports = 1
                next
            }
            !in_ports { next }
            /^[[:space:]]*($|#)/ { next }
            {
                match($0, /^[[:space:]]*/)
                indent = RLENGTH
                if (indent < ports_indent || (indent == ports_indent && $0 !~ /^[[:space:]]*-/)) {
                    in_ports = 0
                }
                if (in_ports && $0 ~ /^[[:space:]]*-[[:space:]]*"?127[.]0[.]0[.]1:/) {
                    printf "%s:%d:%s\n", source, FNR, $0
                }
            }
        ' "$file"
    done < <(find "$compose_dir" -type f -name 'compose.yaml' -print0)
}

# Inspect only published-port lists. Healthcheck commands may legitimately use
# container-internal loopback and must not be rewritten or reported.
cat > "$TEST_FIXTURES/compose.yaml" <<'YAML'
services:
  fixture:
    ports:
      - "127.0.0.1:${TEST_PORT:-1234}:8080"
    healthcheck:
      test:
        - CMD
        - 127.0.0.1:8080
YAML
FIXTURE_OFFENDERS="$(find_loopback_port_bindings "$TEST_FIXTURES")"
[[ "$FIXTURE_OFFENDERS" == *'127.0.0.1:${TEST_PORT:-1234}:8080'* ]] \
    || { echo "  ${RED}FAIL${NC} published loopback fixture was not detected"; exit 1; }
[[ "$FIXTURE_OFFENDERS" != *'127.0.0.1:8080'* ]] \
    || { echo "  ${RED}FAIL${NC} healthcheck loopback was reported as a host bind"; exit 1; }

OFFENDERS="$(find_loopback_port_bindings "$EXT_DIR")"

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
