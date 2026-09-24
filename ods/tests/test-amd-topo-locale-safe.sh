#!/usr/bin/env bash
# Regression guard for issue #5648: the VRAM value rendered by
# installers/lib/amd-topo.sh is embedded in a TSV and later converted with
# jq `tonumber`. awk's printf "%.1f" honours LC_NUMERIC on awks that apply
# the host locale, so a decimal-comma locale (de_DE, fr_FR, ...) emits
# "24,0" and the whole topology JSON fails to build.
#
# Mirrors tests/test-locale-safe-cpu-formatting.sh (#1662): the pin is a
# contract — a static check plus a functional probe when the host has a
# decimal-comma locale installed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

TOPO="$ROOT_DIR/installers/lib/amd-topo.sh"

echo ""
echo "AMD topology VRAM locale safety (#5648)"
echo "---------------------------------------"

# 1. Contract: every printf float awk in amd-topo.sh must be LC_ALL=C-pinned.
if grep -nE 'awk .*printf "%\.' "$TOPO" | grep -Fv 'LC_ALL=C' >/dev/null; then
    fail "amd-topo.sh renders a float with an unpinned awk:"
    grep -nE 'awk .*printf "%\.' "$TOPO" | grep -Fv 'LC_ALL=C' | sed 's/^/       /' >&2
else
    pass "every float-rendering awk in amd-topo.sh is LC_ALL=C-scoped"
fi

# 2. Functional: under a decimal-comma locale the rendered value still uses
#    a dot decimal so jq's tonumber can parse it. Only meaningful on hosts
#    whose awk honours LC_NUMERIC; otherwise the pin is simply inert.
comma_locale=""
for cand in de_DE.UTF-8 de_DE.utf8 fr_FR.UTF-8 es_ES.UTF-8 pt_BR.UTF-8 it_IT.UTF-8 ru_RU.UTF-8 nl_NL.UTF-8; do
    if locale -a 2>/dev/null | grep -qx "$cand"; then
        comma_locale="$cand"
        break
    fi
done

if [[ -n "$comma_locale" ]]; then
    # Same expression as amd-topo.sh (24 GiB → "24.0"), run under the comma
    # locale both pinned and unpinned.
    pinned=$(
        export LC_ALL="$comma_locale"
        vram_bytes=25769803776
        # Identical invocation to amd-topo.sh: the pin lives inside the
        # command, so the parent's comma locale cannot reach it.
        LC_ALL=C awk -v bytes="$vram_bytes" 'BEGIN { printf "%.1f", bytes / 1073741824 }'
    )
    if [[ "$pinned" == "24.0" ]]; then
        pass "pinned VRAM renders dot-decimal under $comma_locale"
    else
        fail "pinned VRAM under $comma_locale rendered '$pinned' (jq tonumber would fail)"
    fi
    # Informational: does this host's awk actually honour LC_NUMERIC?
    unpinned=$(LC_ALL="$comma_locale" awk -v bytes=25769803776 \
        'BEGIN { printf "%.1f", bytes / 1073741824 }' 2>/dev/null)
    echo "  (host awk under $comma_locale renders unpinned: '$unpinned')"
else
    pass "no decimal-comma locale installed; static contract still verified"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
exit "$FAILED"
