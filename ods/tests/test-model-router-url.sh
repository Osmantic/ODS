#!/usr/bin/env bash
# ============================================================================
# model-router endpoint references
# ----------------------------------------------------------------------------
# The model-router service listens on its manifest-declared internal port
# (9099). Any production code that names the model-router container on another
# port — e.g. the stale :4010 fallback in the dashboard-api route-evidence
# proxy — yields connection-refused the moment MODEL_ROUTER_URL is unset.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROUTER_MANIFEST="$ROOT_DIR/extensions/services/model-router/manifest.yaml"
MODEL_ROUTES="$ROOT_DIR/extensions/services/dashboard-api/routers/model_routes.py"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== model-router endpoint reference tests ==="
echo ""

# Ground truth: the manifest-declared internal listener port.
router_port="$(grep -E '^  port:' "$ROUTER_MANIFEST" | head -1 | grep -oE '[0-9]+')"
if [[ "$router_port" == "9099" ]]; then
    pass "model-router manifest internal port is 9099"
else
    fail "model-router manifest internal port drifted (got: ${router_port:-none})"
fi

# The dashboard-api fallback must default to the manifest port, not a stale
# historical port — compose injects MODEL_ROUTER_URL, but bare runs and any
# deployment path without the env var rely on this default.
if grep -qE "MODEL_ROUTER_URL\", \"http://model-router:${router_port}\"\)" "$MODEL_ROUTES"; then
    pass "dashboard-api MODEL_ROUTER_URL fallback uses the manifest port"
else
    fail "dashboard-api MODEL_ROUTER_URL fallback does not match manifest port ${router_port}"
fi

# No production file may reference the model-router container on a stale port.
stale="$(grep -rIhoE 'model-router:[0-9]+' \
    "$ROOT_DIR/extensions/services/dashboard-api/routers" \
    "$ROOT_DIR/docker-compose.base.yml" \
    | sort -u | grep -v "model-router:${router_port}" || true)"
if [[ -z "$stale" ]]; then
    pass "all production model-router references use the manifest port"
else
    fail "production references model-router on wrong port(s): $stale"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
