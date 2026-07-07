#!/usr/bin/env bash
# Regression guard for issue #1614: CPU budget values written to .env must
# always use dot decimals, even when the parent shell has a decimal-comma locale.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
TMP_DIR=""

cleanup() {
    [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

require_scoped_awk() {
    local file="$1"
    local label="$2"
    if grep -Eq 'LC_ALL=C[[:space:]]+awk[[:space:]]+-v[[:space:]]+desired=' "$ROOT_DIR/$file"; then
        pass "$label scopes CPU formatting awk to C locale"
    else
        fail "$label must use LC_ALL=C for CPU formatting awk"
    fi
}

echo ""
echo "Locale-safe CPU formatting"
echo "--------------------------"

require_scoped_awk "installers/phases/06-directories.sh" "Linux phase 06"
require_scoped_awk "installers/macos/lib/env-generator.sh" "macOS env generator"
require_scoped_awk "installers/macos/ods-macos.sh" "macOS installer"

TMP_DIR="$(mktemp -d)"
cat > "$TMP_DIR/awk" <<'EOF'
#!/usr/bin/env bash
if [[ "${LC_ALL:-${LC_NUMERIC:-}}" == "C" ]]; then
    printf '4.0\n'
else
    printf '4,0\n'
fi
EOF
chmod +x "$TMP_DIR/awk"

result="$(
    PATH="$TMP_DIR:$PATH" LC_ALL=fr_FR.UTF-8 bash -c '
        source "$1"
        cap_cpu_value 8.0 4
    ' _ "$ROOT_DIR/installers/macos/lib/env-generator.sh"
)"

if [[ "$result" == "4.0" ]]; then
    pass "cap_cpu_value emits dot decimals under decimal-comma parent locale"
else
    fail "cap_cpu_value emitted '$result' instead of '4.0'"
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All locale-safe CPU formatting tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}Locale-safe CPU formatting tests failed${NC} ($PASSED passed, $FAILED failed)"
    exit 1
fi
