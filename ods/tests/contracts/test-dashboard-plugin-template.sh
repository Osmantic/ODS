#!/usr/bin/env bash
# Contract (issue #3994): the dashboard plugin template must actually build
# and import when copied under src/plugins/<name>/.
#
# Two faults are guarded against:
#   1. JSX inside a .js file — @vitejs/plugin-react only transforms .jsx/.tsx,
#      so JSX in .js fails to parse at build time. Convention (core.js):
#      registration in .js, page components lazy-imported from .jsx.
#   2. A registry import specifier that resolves to no file.
#      From src/plugins/<name>/ the correct specifier is '../registry'.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

TEMPLATE_DIRS=(
    "$ROOT_DIR/extensions/templates"
    "$ROOT_DIR/extensions/library/templates"
)

# ── Both template copies exist and are byte-identical ──────────────────────
for f in dashboard-plugin-template.js dashboard-plugin-template-page.jsx; do
    for d in "${TEMPLATE_DIRS[@]}"; do
        [[ -f "$d/$f" ]] || fail "missing template file: $d/$f"
    done
    cmp -s "${TEMPLATE_DIRS[0]}/$f" "${TEMPLATE_DIRS[1]}/$f" \
        || fail "template copies diverged: $f"
done
pass "both template copies exist and are byte-identical"

# ── No JSX inside any .js template ─────────────────────────────────────────
for d in "${TEMPLATE_DIRS[@]}"; do
    while IFS= read -r js; do
        if grep -nE 'className=|return \(' "$js" >/dev/null; then
            fail "JSX found in .js template (breaks vite build): $js"
        fi
    done < <(find "$d" -maxdepth 1 -name '*.js')
done
pass "no JSX in .js template files"

# ── Registry import resolves from src/plugins/<name>/ ──────────────────────
entry="$ROOT_DIR/extensions/templates/dashboard-plugin-template.js"
spec="$(grep -oE "from '[^']*registry[^']*'" "$entry" | head -1 | tr -d "'")" || true
spec="${spec#from }"
[[ "$spec" == "../registry" ]] \
    || fail "registry import specifier is '$spec'; expected '../registry' for src/plugins/<name>/ placement"
# Resolve '../registry' the way ESM would from src/plugins/<name>/:
[[ -f "$ROOT_DIR/extensions/services/dashboard/src/plugins/registry.js" ]] \
    || fail "../registry does not resolve to src/plugins/registry.js"
pass "registry import specifier '../registry' resolves from src/plugins/<name>/"

# ── Page component is lazy-imported and the .jsx exists alongside ──────────
grep -qE "lazy\(\(\) => import\('\./[A-Za-z0-9_-]+'\)\)" "$entry" \
    || fail "template does not lazy-import its page component"
jsx_target="$(grep -oE "import\('\./([A-Za-z0-9_-]+)'\)" "$entry" | sed -E "s/.*'\.\/(.*)'.*/\1/" | head -1)"
[[ -n "$jsx_target" ]] || fail "could not determine lazy import target"
for d in "${TEMPLATE_DIRS[@]}"; do
    [[ -f "$d/$jsx_target.jsx" ]] || fail "lazy import target missing: $d/$jsx_target.jsx"
    grep -q 'export default' "$d/$jsx_target.jsx" \
        || fail "page component lacks default export: $d/$jsx_target.jsx"
done
pass "page component is lazy-imported and ships as a .jsx with default export"

# ── Template matches the core.js convention it cites ───────────────────────
grep -q "lazy(() => import" "$ROOT_DIR/extensions/services/dashboard/src/plugins/core.js" \
    || fail "core.js no longer lazy-imports pages — convention reference is stale"
pass "core.js lazy-import convention still holds"

echo "All dashboard plugin template contract checks passed."
