#!/bin/bash
# ============================================================================
# ODS — Lifecycle Hooks Test Suite
# ============================================================================
# Tests hook resolution priority in service-registry.sh (SERVICE_SETUP_HOOKS)
# and validates the _run_hook helper pattern.
#
# Usage: bash tests/test-hooks.sh
# Exit 0 if all pass, 1 if any fail
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() {
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}SKIP${NC}  $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..60})${NC}"
}

# Check prerequisites
if (( BASH_VERSINFO[0] < 4 )); then
    echo "SKIP: Bash 4+ required (have $BASH_VERSION)"
    exit 0
fi

if ! command -v python3 &>/dev/null; then
    echo "SKIP: python3 not found"
    exit 0
fi

if ! python3 -c "import yaml" 2>/dev/null; then
    echo "SKIP: PyYAML not available"
    exit 0
fi

# ============================================
# TEST 1: SERVICE_SETUP_HOOKS prefers hooks.post_install
# ============================================
header "1/4" "hooks.post_install priority over setup_hook"

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Create a test extension with both setup_hook and hooks.post_install
EXT_DIR="$TMPDIR_TEST/extensions/services/test-ext"
mkdir -p "$EXT_DIR"
cat > "$EXT_DIR/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: test-ext
  name: Test Extension
  port: 9999
  health: /health
  setup_hook: old-setup.sh
  hooks:
    post_install: hooks/new-setup.sh
YAML
touch "$EXT_DIR/old-setup.sh"
mkdir -p "$EXT_DIR/hooks"
touch "$EXT_DIR/hooks/new-setup.sh"

export SCRIPT_DIR="$PROJECT_DIR"
export EXTENSIONS_DIR="$TMPDIR_TEST/extensions/services"

# Source registry and load
# Reset loaded flag
_SR_LOADED=false
. "$PROJECT_DIR/lib/service-registry.sh"
sr_load 2>/dev/null

HOOK_PATH="${SERVICE_SETUP_HOOKS[test-ext]:-}"
if [[ "$HOOK_PATH" == *"hooks/new-setup.sh" ]]; then
    pass "SERVICE_SETUP_HOOKS prefers hooks.post_install"
else
    fail "SERVICE_SETUP_HOOKS should prefer hooks.post_install" "got: $HOOK_PATH"
fi

# ============================================
# TEST 2: setup_hook fallback when hooks map absent
# ============================================
header "2/4" "setup_hook fallback when hooks map absent"

EXT_DIR2="$TMPDIR_TEST/extensions/services/test-ext2"
mkdir -p "$EXT_DIR2"
cat > "$EXT_DIR2/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: test-ext2
  name: Test Extension 2
  port: 9998
  health: /health
  setup_hook: legacy-setup.sh
YAML
touch "$EXT_DIR2/legacy-setup.sh"

_SR_LOADED=false
sr_load 2>/dev/null

HOOK_PATH2="${SERVICE_SETUP_HOOKS[test-ext2]:-}"
if [[ "$HOOK_PATH2" == *"legacy-setup.sh" ]]; then
    pass "Falls back to setup_hook when hooks map absent"
else
    fail "Should fall back to setup_hook" "got: $HOOK_PATH2"
fi

# ============================================
# TEST 3: No hook set → empty
# ============================================
header "3/4" "No hook set returns empty"

EXT_DIR3="$TMPDIR_TEST/extensions/services/test-ext3"
mkdir -p "$EXT_DIR3"
cat > "$EXT_DIR3/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: test-ext3
  name: Test Extension 3
  port: 9997
  health: /health
YAML

_SR_LOADED=false
sr_load 2>/dev/null

HOOK_PATH3="${SERVICE_SETUP_HOOKS[test-ext3]:-}"
if [[ -z "$HOOK_PATH3" ]]; then
    pass "No hook set → empty string"
else
    fail "Expected empty hook path" "got: $HOOK_PATH3"
fi

# ============================================
# TEST 4: _run_hook resolves and validates
# ============================================
header "4/4" "_run_hook Python resolver validates path containment"

# Create test extension with path traversal attempt
EXT_DIR4="$TMPDIR_TEST/extensions/services/test-ext4"
mkdir -p "$EXT_DIR4"
cat > "$EXT_DIR4/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: test-ext4
  name: Test Extension 4
  port: 9996
  health: /health
  hooks:
    pre_start: ../../../etc/passwd
YAML

# Run the Python resolver and check it rejects traversal
INSTALL_DIR="$TMPDIR_TEST"
RESULT=$(python3 - "$EXT_DIR4" "pre_start" <<'PYEOF' 2>&1 || true
import yaml, sys
from pathlib import Path

ext_dir = Path(sys.argv[1])
hook_name = sys.argv[2]

manifest_path = ext_dir / "manifest.yaml"
with open(manifest_path) as f:
    m = yaml.safe_load(f)
service = m.get("service", {})
hooks = service.get("hooks", {})
hook_script = hooks.get(hook_name, "")
if not hook_script:
    sys.exit(0)
hook_path = (ext_dir / hook_script).resolve()
try:
    hook_path.relative_to(ext_dir.resolve())
except ValueError:
    print("ERROR: hook path escapes extension directory", file=sys.stderr)
    sys.exit(1)
print(str(hook_path))
PYEOF
)

if [[ "$RESULT" == *"ERROR"* ]]; then
    pass "Path traversal in hook script is rejected"
else
    fail "Path traversal should be rejected" "got: $RESULT"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}════════════════════════════════════════${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "  ${GREEN}$PASS passed${NC}  ${RED}$FAIL failed${NC}  ${YELLOW}$SKIP skipped${NC}  ($TOTAL total)"
echo -e "${BOLD}════════════════════════════════════════${NC}"

[[ $FAIL -eq 0 ]]
