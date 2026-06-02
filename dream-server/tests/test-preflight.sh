#!/bin/bash
# ============================================================================
# Dream Server dream-preflight.sh Test Suite
# ============================================================================
# Validates that dream-preflight.sh is syntactically correct, follows the
# project's shell style requirements, and encodes the correct LLM port
# default (8080, not 11434).
#
# These tests are static (no running Docker) — they inspect the script itself.
# Integration-level tests (actual service probing) are covered in
# tests/test-health-check.sh and tests/test-integration.sh.
#
# Usage: bash tests/test-preflight.sh
# Exit codes: 0 = all pass, 1 = one or more failures
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFLIGHT="$ROOT_DIR/dream-preflight.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }
skip() { printf "  ${YELLOW}⊘ SKIP${NC} %s\n" "$1"; }

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║   dream-preflight.sh Test Suite               ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# ── Static checks ──────────────────────────────────────────────────────────

# 1. Script exists
if [[ ! -f "$PREFLIGHT" ]]; then
    fail "dream-preflight.sh not found at $PREFLIGHT"
    echo ""
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi
pass "dream-preflight.sh exists"

# 2. Script passes bash syntax check
if bash -n "$PREFLIGHT" 2>/dev/null; then
    pass "bash -n syntax check passes"
else
    fail "bash -n syntax check failed"
fi

# 3. Uses set -euo pipefail (project style requirement per CONTRIBUTING.md)
if grep -q 'set -euo pipefail' "$PREFLIGHT"; then
    pass "set -euo pipefail present"
else
    fail "set -euo pipefail missing — CONTRIBUTING.md requires this in all bash files"
fi

# 4. LLM port default is 8080, NOT 11434
# config/ports.json and docker-compose.base.yml define the canonical default as 8080.
# 11434 is only the Strix Halo AMD override (written to .env by phase 06).
# The fallback in the script must be 8080 so plain installs without OLLAMA_PORT in
# .env get the correct port probed.
if grep -q 'LLAMA_SERVER_PORT:-8080' "$PREFLIGHT"; then
    pass "LLM port fallback is 8080 (aligns with config/ports.json)"
else
    fail "LLM port fallback is not 8080 — check the OLLAMA_PORT expansion in dream-preflight.sh"
fi

# 5. Does NOT contain the old wrong fallback of 11434 in port resolution
if grep -q 'LLAMA_SERVER_PORT:-11434' "$PREFLIGHT"; then
    fail "Old wrong LLM port fallback 11434 still present — should be 8080"
else
    pass "Old wrong LLM port fallback 11434 is gone"
fi

# 6. detect_backend function is present
if grep -q 'detect_backend()' "$PREFLIGHT"; then
    pass "detect_backend function present"
else
    fail "detect_backend function missing"
fi

# 7. AMD sysfs scan iterates all DRM cards (not just card1)
if grep -q '/sys/class/drm/card\*/device' "$PREFLIGHT"; then
    pass "AMD sysfs scan uses glob (all DRM cards)"
else
    fail "AMD sysfs scan missing wildcard — may miss some AMD GPUs"
fi

# 8. Script uses BASH_SOURCE for portability (not $0)
if grep -q 'BASH_SOURCE' "$PREFLIGHT"; then
    pass "Uses BASH_SOURCE for script dir resolution"
else
    fail "Missing BASH_SOURCE — \$0 breaks when script is sourced"
fi

# 9. Summary section has all expected check sections
for check_label in "Checking Docker" "Checking Docker Compose" "Checking GPU" \
                   "Checking LLM endpoint" "Checking Whisper" "Checking TTS" \
                   "Checking Embeddings" "Checking Dashboard"; do
    if grep -q "$check_label" "$PREFLIGHT"; then
        pass "Check section present: $check_label"
    else
        fail "Check section missing: $check_label"
    fi
done

# 10. Uses docker port to probe actual external port mapping (not just hardcoded)
if grep -q 'docker port dream-llama-server' "$PREFLIGHT"; then
    pass "Probes actual Docker port mapping via 'docker port'"
else
    fail "Does not probe actual Docker port mapping"
fi

# 11. External Lemonade mode checks LiteLLM, not a managed llama-server container
if grep -q 'is_external_lemonade()' "$PREFLIGHT" \
   && grep -q 'LiteLLM external Lemonade gateway' "$PREFLIGHT" \
   && grep -q 'dream-litellm' "$PREFLIGHT"; then
    pass "External Lemonade preflight checks LiteLLM gateway"
else
    fail "External Lemonade preflight must not require managed dream-llama-server"
fi

# ── Runtime smoke test (no Docker required) ─────────────────────────────────

# 12. Script runs to completion without unbound variable or syntax errors
#     (Services won't be up, so we expect exit 1 — that is correct behavior)
set +e
err_output=$(
    SERVICE_HOST=localhost \
    OLLAMA_PORT="" \
    LLAMA_SERVER_PORT="" \
    GPU_BACKEND="" \
    bash "$PREFLIGHT" 2>&1
)
run_exit=$?
set -e

if echo "$err_output" | grep -qiE 'unbound variable|syntax error|command not found: \['; then
    fail "Script produced shell error: $(echo "$err_output" | grep -iE 'unbound variable|syntax error' | head -1)"
else
    pass "Script runs without shell errors (exit $run_exit is expected)"
fi

# 13. Exit code is 0 or 1; never an unexpected crash code
if [[ "$run_exit" -eq 0 ]] || [[ "$run_exit" -eq 1 ]]; then
    pass "Exit code is valid (0=pass, 1=fail): $run_exit"
else
    fail "Unexpected exit code $run_exit — script may have crashed"
fi

# ── Summary ────────────────────────────────────────────────────────────────

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
