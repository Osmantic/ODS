#!/bin/bash
# ============================================================================
# Dream Server health_type Fixtures Test Suite
# ============================================================================
# Validates health_type behavior across schema, validation, and audit.
#
# Usage: ./tests/contracts/test-health-type-fixtures.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/extensions/schema"
LIB_SCHEMA_DIR="${SCRIPT_DIR}/extensions/library/schema"
TEST_TMPDIR=$(mktemp -d)

PASS=0
FAIL=0

cleanup() {
    rm -rf "$TEST_TMPDIR"
}
trap cleanup EXIT

assert_pass() {
    local desc="$1"
    echo "  ✅ PASS: $desc"
    ((PASS++)) || true
}

assert_fail() {
    local desc="$1"
    echo "  ❌ FAIL: $desc"
    ((FAIL++)) || true
}

make_manifest() {
    local health_type="${1:-http}"
    local port="${2:-8080}"
    local health="${3:-/health}"
    local startup_check="${4:-true}"

    cat > "${TEST_TMPDIR}/manifest.yaml" <<EOF
schema_version: dream.services.v1
service:
  id: test-service
  name: Test Service
  port: ${port}
  health: "${health}"
  health_type: "${health_type}"
  startup_check: ${startup_check}
  type: docker
  category: optional
  description: "Test service"
EOF
}

# ── JSON Schema tests ─────────────────────────────────────────────────────────

echo ""
echo "=== JSON Schema Tests ==="

PY=""
for p in "${HOME}/.hermes/job-search/.venv/bin/python3" python3; do
    if "$p" -c "import json,yaml" 2>/dev/null; then
        PY="$p"
        break
    fi
done

if [[ -n "$PY" ]]; then
    for ht in http tcp none; do
        make_manifest "$ht" 8080 "/health" true
        if $PY -c "
import json, yaml, sys
with open('${SCHEMA_DIR}/service-manifest.v1.json') as f: schema = json.load(f)
with open('${TEST_TMPDIR}/manifest.yaml') as f: doc = yaml.safe_load(f)
allowed = schema['properties']['service']['properties']['health_type']['enum']
if doc['service']['health_type'] not in allowed: sys.exit(1)
" 2>/dev/null; then
            assert_pass "schema accepts health_type=${ht}"
        else
            assert_fail "schema should accept health_type=${ht}"
        fi
    done

    # Invalid health_type rejected by schema
    make_manifest "invalid" 8080 "/health" true
    if $PY -c "
import json, yaml, sys
with open('${SCHEMA_DIR}/service-manifest.v1.json') as f: schema = json.load(f)
with open('${TEST_TMPDIR}/manifest.yaml') as f: doc = yaml.safe_load(f)
allowed = schema['properties']['service']['properties']['health_type']['enum']
if doc['service']['health_type'] not in allowed: sys.exit(1)
" 2>/dev/null; then
        assert_fail "schema should reject health_type=invalid"
    else
        assert_pass "schema rejects health_type=invalid"
    fi

    # Library schema has health_type
    if $PY -c "
import json, sys
with open('${LIB_SCHEMA_DIR}/service-manifest.v1.json') as f: schema = json.load(f)
props = schema['properties']['service']['properties']
assert 'health_type' in props
assert props['health_type']['enum'] == ['http', 'tcp', 'none']
" 2>/dev/null; then
        assert_pass "library schema has health_type enum"
    else
        assert_fail "library schema should have health_type enum"
    fi
else
    echo "  ⚠️  Python+yaml not available, skipping"
fi

# ── Fixture file tests ─────────────────────────────────────────────────────────

echo ""
echo "=== Fixture File Tests ==="

PIPER="${SCRIPT_DIR}/extensions/library/services/piper-audio/manifest.yaml"
if [[ -f "$PIPER" ]] && grep -q "health_type: tcp" "$PIPER"; then
    assert_pass "piper-audio has health_type=tcp"
else
    assert_fail "piper-audio should have health_type=tcp"
fi

AIDER="${SCRIPT_DIR}/extensions/library/services/aider/manifest.yaml"
if [[ -f "$AIDER" ]] && grep -q "health_type: none" "$AIDER"; then
    assert_pass "aider has health_type=none"
else
    assert_fail "aider should have health_type=none"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "========================================"
[[ "$FAIL" -gt 0 ]] && exit 1
exit 0
