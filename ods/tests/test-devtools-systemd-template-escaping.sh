#!/bin/bash
# Regression: 07-devtools.sh rendered the ods-host-agent.service and
# ods-mdns.service systemd units by interpolating INSTALL_DIR/HOME/the
# resolved agent user/python path directly into a sed replacement with no
# escaping — inconsistent with the opencode-web.service block 30 lines
# above it in the same file, which explicitly escapes for exactly this
# reason ("Escape sed special chars to prevent injection from path
# values"). An unescaped '&' in any of those values (e.g. a custom
# INSTALL_DIR) re-inserts the whole matched line into the rendered unit
# instead of the real value.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/installers/phases/07-devtools.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { echo -e "  ${GREEN}✓${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "=== 07-devtools.sh systemd template escaping ==="
echo ""

[[ -f "$TARGET" ]] || { fail "missing $TARGET"; exit 1; }
if bash -n "$TARGET"; then
    pass "07-devtools.sh passes bash -n"
else
    fail "07-devtools.sh bash -n failed"
fi

# Static check: both the host-agent and mdns unit substitutions must use an
# escaped variable, not the raw INSTALL_DIR/HOME/etc.
for marker in '_install_dir_esc=$(printf' '_home_esc=$(printf' '_agent_python_esc=$(printf' '_agent_user_esc=$(printf' '_mdns_python_esc=$(printf'; do
    if grep -qF "$marker" "$TARGET"; then
        pass "found escaping for: ${marker%%=*}"
    else
        fail "missing escaping for: ${marker%%=*}"
    fi
done

for substitution in '__INSTALL_DIR__|${_install_dir_esc}' '__HOME__|${_home_esc}' '__PYTHON3__|${_agent_python_esc}' '__INSTALL_USER__|${_agent_user_esc}'; do
    count="$(grep -cF "$substitution" "$TARGET" || true)"
    if [[ "${count:-0}" -ge 1 ]]; then
        pass "sed substitution uses escaped value: $substitution"
    else
        fail "sed substitution does not use escaped value: $substitution"
    fi
done

# Direct reproduction of the escaping expression itself (pure, self-contained
# string transform — no need to extract anything from the file).
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

printf 'ExecStart=__PYTHON3__ __INSTALL_DIR__/bin/agent.py\nWorkingDirectory=__INSTALL_DIR__\nUser=__INSTALL_USER__\n' > "$TMP_DIR/unit.rendered"

install_dir_with_amp='/opt/my&installs/ods'
install_dir_esc=$(printf '%s\n' "$install_dir_with_amp" | sed 's/[&/\]/\\&/g')
sed -i "s|__INSTALL_DIR__|${install_dir_esc}|g" "$TMP_DIR/unit.rendered" 2>/dev/null || \
    sed -i '' "s|__INSTALL_DIR__|${install_dir_esc}|g" "$TMP_DIR/unit.rendered"

rendered_line="$(grep '^WorkingDirectory=' "$TMP_DIR/unit.rendered")"
if [[ "$rendered_line" == "WorkingDirectory=${install_dir_with_amp}" ]]; then
    pass "a value containing '&' round-trips correctly through the escaped substitution"
else
    fail "a value containing '&' was corrupted (got: $rendered_line)"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
