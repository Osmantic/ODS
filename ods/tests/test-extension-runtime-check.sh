#!/bin/bash
# ============================================================================
# ODS extension-runtime-check.sh Test Suite
# ============================================================================
# Ensures scripts/extension-runtime-check.sh is syntactically valid and runs
# without error against the repo (non-blocking when Docker is absent).
#
# Usage: bash tests/test-extension-runtime-check.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHK="$ROOT_DIR/scripts/extension-runtime-check.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; SKIPPED=$((SKIPPED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║   extension-runtime-check.sh Test Suite          ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$CHK" ]]; then
    fail "scripts/extension-runtime-check.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; [[ $FAILED -eq 0 ]]; exit $?
fi
pass "extension-runtime-check.sh exists"

if ! bash -n "$CHK"; then
    fail "bash -n reported syntax errors"
else
    pass "bash -n clean"
fi

set +e
out="$(cd "$ROOT_DIR" && bash "$CHK" "$ROOT_DIR" 2>&1)"
code=$?
set -e

if [[ $code -ne 0 ]]; then
    fail "default run exited $code (expected 0 — non-blocking)"
    echo "$out" | head -20
else
    pass "default run exits 0"
fi

if [[ "$out" != *"Extension runtime check"* ]]; then
    fail "expected header line in output"
else
    pass "output mentions extension runtime check"
fi

if docker info >/dev/null 2>&1; then
    if echo "$out" | grep -qE '\[OK\]|\[BAD\]|\[INFO\]'; then
        pass "docker available — check lines present"
    elif echo "$out" | grep -q "nothing to check"; then
        pass "docker available but no services registered — script exits cleanly"
    else
        fail "docker available but output contained no expected status lines"
    fi
else
    if echo "$out" | grep -qi docker; then
        pass "docker unavailable — script explains skip"
    else
        fail "docker unavailable but output did not mention docker"
    fi
fi

# ============================================================================
# STRICT mode tests — simulate a running container with a failing health probe
# ============================================================================
# Strategy:
#   1. Build a minimal fixture ODS root containing lib/service-registry.sh,
#      a manifest with one optional extension that has a health path, and a
#      stub compose.yaml so the registry sees it as enabled.
#   2. Inject a mock PATH with a docker that reports the container as "running"
#      and a curl that always returns exit 1 (probe failure).
#   3. Assert exit 0 in normal mode and exit 1 in STRICT mode.

FIXTURE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ods-strict-test.XXXXXX")

strict_cleanup() { rm -rf "$FIXTURE_DIR"; }
trap strict_cleanup EXIT

# Mirror the lib/ directory the script needs
mkdir -p "$FIXTURE_DIR/lib"
cp "$ROOT_DIR/lib/service-registry.sh" "$FIXTURE_DIR/lib/service-registry.sh"
[[ -f "$ROOT_DIR/lib/safe-env.sh" ]] && cp "$ROOT_DIR/lib/safe-env.sh" "$FIXTURE_DIR/lib/safe-env.sh"

# Minimal extension directory with a manifest and stub compose
EXT_DIR="$FIXTURE_DIR/extensions/services/test-svc"
mkdir -p "$EXT_DIR"
cat > "$EXT_DIR/manifest.yaml" << 'MANIFEST'
schema_version: ods.services.v1
compatibility:
  ods_min: "2.0.0"
service:
  id: test-svc
  name: Test Service
  aliases: []
  container_name: ods-test-svc
  default_host: test-svc
  port: 19999
  external_port_env: TEST_SVC_PORT
  external_port_default: 19999
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: []
MANIFEST
cat > "$EXT_DIR/compose.yaml" << 'COMPOSE'
services:
  test-svc:
    image: alpine
    container_name: ods-test-svc
COMPOSE

# Create a mock bin directory with stub docker and curl executables
MOCK_BIN=$(mktemp -d "${TMPDIR:-/tmp}/ods-strict-mock-bin.XXXXXX")

# docker stub: "info" → exit 0 (daemon reachable); "inspect" → exit 0 (container exists);
#              "inspect -f {{.State.Status}}" → "running"
cat > "$MOCK_BIN/docker" << 'DOCKER_STUB'
#!/bin/bash
case "${1:-}" in
    info)   exit 0 ;;
    inspect)
        if [[ "${*}" == *"{{.State.Status}}"* ]]; then
            echo "running"
        fi
        exit 0
        ;;
    *)      exit 0 ;;
esac
DOCKER_STUB
chmod +x "$MOCK_BIN/docker"

# curl stub: always fails (simulates a health probe timeout/404)
cat > "$MOCK_BIN/curl" << 'CURL_STUB'
#!/bin/bash
exit 1
CURL_STUB
chmod +x "$MOCK_BIN/curl"

# Run in normal mode — should exit 0 even though the health probe fails
set +e
out_normal=$(PATH="$MOCK_BIN:$PATH" bash "$CHK" "$FIXTURE_DIR" 2>&1)
code_normal=$?
set -e

if [[ $code_normal -eq 0 ]]; then
    pass "STRICT=0 (default): exits 0 even when health probe fails"
else
    fail "STRICT=0 (default): expected exit 0 on probe failure, got $code_normal"
fi

if echo "$out_normal" | grep -q "\[BAD\]"; then
    pass "STRICT=0: [BAD] line emitted for failing probe"
else
    fail "STRICT=0: expected [BAD] line in output, got: $(echo "$out_normal" | tail -5)"
fi

# Run in STRICT mode — must exit 1 when a running container fails its health probe
set +e
out_strict=$(PATH="$MOCK_BIN:$PATH" EXTENSION_RUNTIME_CHECK_STRICT=1 bash "$CHK" "$FIXTURE_DIR" 2>&1)
code_strict=$?
set -e

if [[ $code_strict -eq 1 ]]; then
    pass "STRICT=1: exits 1 on health probe failure"
else
    fail "STRICT=1: expected exit 1 on probe failure, got $code_strict"
fi

if echo "$out_strict" | grep -q "\[BAD\]"; then
    pass "STRICT=1: [BAD] line emitted for failing probe"
else
    fail "STRICT=1: expected [BAD] line in output, got: $(echo "$out_strict" | tail -5)"
fi

rm -rf "$MOCK_BIN"

echo ""
echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[[ $FAILED -eq 0 ]]
