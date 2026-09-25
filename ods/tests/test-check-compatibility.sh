#!/bin/bash
# ============================================================================
# ODS check-compatibility.sh Test Suite
# ============================================================================
# Ensures scripts/check-compatibility.sh correctly validates the compatibility
# contracts declared in manifest.json (structure, compose canonical files,
# workflow catalog path, extension schema, ports contract) and its --help.
#
# Usage: ./tests/test-check-compatibility.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK_COMPAT_BASH="${BASH:-$(command -v bash)}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   check-compatibility.sh Test Suite            ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$ROOT_DIR/scripts/check-compatibility.sh" ]]; then
    fail "scripts/check-compatibility.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "check-compatibility.sh exists"

if ! command -v jq &>/dev/null; then
    fail "jq is required for check-compatibility.sh"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "jq available"

# 1. --help prints usage and exits 0 without touching manifest.json.
set +e
out=$("$CHECK_COMPAT_BASH" "$ROOT_DIR/scripts/check-compatibility.sh" --help 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]] && echo "$out" | grep -qi "usage"; then
    pass "--help prints usage and exits 0"
else
    fail "--help should print usage and exit 0, got exit $r"
fi

# 2. Real repo manifest.json passes end-to-end.
set +e
out=$("$CHECK_COMPAT_BASH" "$ROOT_DIR/scripts/check-compatibility.sh" 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Real manifest.json validates cleanly"
else
    fail "Real manifest.json should validate cleanly, got exit $r: $out"
fi

# Sandbox: copy the script into a fake ROOT_DIR so MANIFEST_FILE (derived from
# the script's own path) resolves to a controllable manifest.json instead of
# the real repo one.
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/scripts" "$TMP_DIR/docs"
cp "$ROOT_DIR/scripts/check-compatibility.sh" "$TMP_DIR/scripts/check-compatibility.sh"

# 3. Missing manifest.json → exit 1.
set +e
"$CHECK_COMPAT_BASH" "$TMP_DIR/scripts/check-compatibility.sh" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 1 ]]; then
    pass "Missing manifest.json yields exit 1"
else
    fail "Missing manifest.json should yield exit 1, got $r"
fi

# 4. manifest.json missing required top-level fields → exit 1.
echo '{}' > "$TMP_DIR/manifest.json"
set +e
out=$("$CHECK_COMPAT_BASH" "$TMP_DIR/scripts/check-compatibility.sh" 2>&1)
r=$?
set -e
if [[ $r -eq 1 ]] && echo "$out" | grep -q "missing required top-level fields"; then
    pass "Manifest missing required fields yields exit 1 with a clear message"
else
    fail "Manifest missing required fields should yield exit 1, got $r: $out"
fi

# 5. Valid structure but a referenced compose contract file doesn't exist → exit 1.
cat > "$TMP_DIR/manifest.json" <<'EOF'
{
  "manifestVersion": "1.0.0",
  "release": {"version": "0.0.0"},
  "compatibility": {"os": {}},
  "contracts": {
    "compose": {"canonical": ["docker-compose.missing.yml"]},
    "workflowCatalog": {"canonicalPath": "workflows/catalog.json"},
    "extensions": {"serviceManifestSchema": "schema/service-manifest.json"},
    "ports": {"canonicalPath": "config/ports.json"}
  }
}
EOF
set +e
out=$("$CHECK_COMPAT_BASH" "$TMP_DIR/scripts/check-compatibility.sh" 2>&1)
r=$?
set -e
if [[ $r -eq 1 ]] && echo "$out" | grep -q "missing compose contract file"; then
    pass "Missing compose contract file is caught before later checks"
else
    fail "Missing compose contract file should yield exit 1, got $r: $out"
fi

# 6. All referenced files present, ports contract has valid structure → exit 0.
touch "$TMP_DIR/docker-compose.missing.yml"
mkdir -p "$TMP_DIR/workflows" "$TMP_DIR/schema" "$TMP_DIR/config"
echo '{}' > "$TMP_DIR/workflows/catalog.json"
echo '{}' > "$TMP_DIR/schema/service-manifest.json"
echo '{"version": "1", "ports": [{"name": "example", "port": 8080}]}' > "$TMP_DIR/config/ports.json"
set +e
out=$("$CHECK_COMPAT_BASH" "$TMP_DIR/scripts/check-compatibility.sh" 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "All contracts present with valid ports structure yields exit 0"
else
    fail "All contracts present should yield exit 0, got $r: $out"
fi

# 7. Ports contract file present but with an invalid structure (empty array) → exit 1.
echo '{"version": "1", "ports": []}' > "$TMP_DIR/config/ports.json"
set +e
out=$("$CHECK_COMPAT_BASH" "$TMP_DIR/scripts/check-compatibility.sh" 2>&1)
r=$?
set -e
if [[ $r -eq 1 ]] && echo "$out" | grep -q "invalid ports contract structure"; then
    pass "Invalid ports contract structure is rejected"
else
    fail "Invalid ports contract structure should yield exit 1, got $r: $out"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
