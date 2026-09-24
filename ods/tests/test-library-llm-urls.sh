#!/usr/bin/env bash
# ============================================================================
# Library extension LLM endpoint references
# ----------------------------------------------------------------------------
# Container-to-container traffic to the bundled inference service uses the
# manifest-declared internal port (llama-server listens on 8080). Library
# compose fallbacks and workflow README instructions that point at another
# port (e.g. the stale :8000 default) break extensions the moment they are
# enabled or followed.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$ROOT_DIR/extensions/library"
LLAMA_MANIFEST="$ROOT_DIR/extensions/services/llama-server/manifest.yaml"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Library LLM endpoint reference tests ==="
echo ""

# Ground truth: the manifest-declared internal listener port.
llama_port="$(grep -E '^  port:' "$LLAMA_MANIFEST" | head -1 | grep -oE '[0-9]+')"
if [[ "$llama_port" == "8080" ]]; then
    pass "llama-server manifest internal port is 8080"
else
    fail "llama-server manifest internal port drifted (got: ${llama_port:-none})"
fi

# Every compose fallback or doc URL that names the llama-server container must
# use the manifest port — a stale port yields connection-refused at runtime.
stale="$(grep -rhoE "llama-server:[0-9]+" "$LIB_DIR" | sort -u | grep -v "llama-server:${llama_port}" || true)"
if [[ -z "$stale" ]]; then
    pass "all library llama-server references use the manifest port"
else
    fail "library references llama-server on wrong port(s): $stale"
fi

# The two fallbacks must interpolate LLM_API_URL before defaulting, matching
# the convention used by docker-compose.base.yml.
for f in services/crewai/compose.yaml services/open-interpreter/compose.yaml; do
    if grep -qE '\$\{LLM_API_URL:-http://llama-server:8080\}' "$LIB_DIR/$f"; then
        pass "$f falls back to the real llama-server endpoint"
    else
        fail "$f is missing the corrected LLM_API_URL fallback"
    fi
done

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
