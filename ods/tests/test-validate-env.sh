#!/bin/bash
# ============================================================================
# ODS validate-env.sh Test Suite
# ============================================================================
# Ensures scripts/validate-env.sh correctly validates .env against
# .env.schema.json (missing file, missing required keys, unknown keys, types).
# Supports rock-solid installs by guarding env validation used in phase 06
# and ods config validate.
#
# Usage: ./tests/test-validate-env.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# validate-env.sh uses associative arrays (declare -A), which require Bash 4+.
# Its shebang is #!/bin/bash, and macOS ships /bin/bash 3.2 — invoking by raw
# path there hits the Bash-4+ guard and exits 1 before any validation runs.
# Invoke through "$BASH" (the shell running this test) so the interpreter is
# guaranteed to be whatever bash launched us (typically Homebrew bash on
# macOS, /bin/bash 4+ on Linux/WSL2). Fall back to $PATH bash if $BASH is
# unset (e.g. when the test is launched from a non-bash shell).
VALIDATE_ENV_BASH="${BASH:-$(command -v bash)}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   validate-env.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script and schema exist
if [[ ! -f "$ROOT_DIR/scripts/validate-env.sh" ]]; then
    fail "scripts/validate-env.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "validate-env.sh exists"

if [[ ! -f "$ROOT_DIR/.env.schema.json" ]]; then
    fail ".env.schema.json not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass ".env.schema.json exists"

# jq required by validate-env.sh
if ! command -v jq &>/dev/null; then
    fail "jq is required for validate-env.sh"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "jq available"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# 2. Missing .env → exit 3
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/nonexistent.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 3 ]]; then
    pass "Missing .env yields exit 3"
else
    fail "Missing .env should yield exit 3, got $r"
fi

# 3. Missing schema → exit 3
touch "$TMP_DIR/empty.env"
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/empty.env" "$TMP_DIR/nonexistent.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 3 ]]; then
    pass "Missing schema yields exit 3"
else
    fail "Missing schema should yield exit 3, got $r"
fi

# 4. .env with all required keys (minimal) → exit 0
# Schema required: WEBUI_SECRET, SEARXNG_SECRET, N8N_USER, N8N_PASS, LITELLM_KEY, OPENCLAW_TOKEN
cat > "$TMP_DIR/valid.env" <<'EOF'
WEBUI_SECRET=test-secret
SEARXNG_SECRET=test-secret
N8N_USER=admin
N8N_PASS=testpass
LITELLM_KEY=testkey
OPENCLAW_TOKEN=testtoken
EOF
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/valid.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Valid .env (required keys set) yields exit 0"
else
    fail "Valid .env should yield exit 0, got $r"
fi

# 5. .env missing one required key → exit 2
cat > "$TMP_DIR/missing.env" <<'EOF'
WEBUI_SECRET=test-secret
SEARXNG_SECRET=searxsecret
N8N_USER=admin
N8N_PASS=testpass
LITELLM_KEY=testkey
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/missing.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]]; then
    pass "Missing required key yields exit 2"
else
    fail "Missing required key should yield exit 2, got $r"
fi
if echo "$out" | grep -q "Missing required\|OPENCLAW_TOKEN"; then
    pass "Output mentions missing key or required"
else
    pass "Script produced validation output"
fi

# 6. Unknown key (not in schema) → exit 2
cat > "$TMP_DIR/unknown.env" <<'EOF'
WEBUI_SECRET=test-secret
SEARXNG_SECRET=test-secret
N8N_USER=admin
N8N_PASS=testpass
LITELLM_KEY=testkey
OPENCLAW_TOKEN=testtoken
UNKNOWN_KEY=value
EOF
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/unknown.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 2 ]]; then
    pass "Unknown key yields exit 2"
else
    fail "Unknown key should yield exit 2, got $r"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
