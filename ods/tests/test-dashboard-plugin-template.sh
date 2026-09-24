#!/usr/bin/env bash
# The dashboard plugin template must be buildable and importable as shipped.
#
# It previously lived in a .js file while defining a component inline (vite
# only applies the JSX transform to .jsx/.tsx) and imported the plugin
# registry through a relative path that resolved nowhere from either of its
# two locations, so a copied template could never build.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASH_SRC="$ROOT_DIR/extensions/services/dashboard/src"
TEMPLATES=(
    "$ROOT_DIR/extensions/templates/dashboard-plugin-template.jsx"
    "$ROOT_DIR/extensions/library/templates/dashboard-plugin-template.jsx"
)

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

# 1. A template that contains JSX must not be a .js file.
for legacy in "$ROOT_DIR/extensions/templates/dashboard-plugin-template.js" \
              "$ROOT_DIR/extensions/library/templates/dashboard-plugin-template.js"; do
    [[ ! -e "$legacy" ]] \
        || fail "$legacy still exists: JSX in a .js file is not transformed by vite"
done
for t in "${TEMPLATES[@]}"; do
    [[ -f "$t" ]] || fail "missing template: $t"
done
pass "the JSX plugin template ships as .jsx, not .js"

# 2. Its registry import must resolve once the template sits in src/plugins/.
for t in "${TEMPLATES[@]}"; do
    spec="$(grep -oE "from '[^']*registry'" "$t" | head -1 | sed -E "s/from '([^']*)'/\1/")"
    [[ -n "$spec" ]] || fail "$t does not import the plugin registry"
    case "$spec" in
        ./*) rel="${spec#./}" ;;
        *)   fail "$t imports the registry as '$spec'; a template copied into src/plugins/ must use './registry'" ;;
    esac
    resolved=""
    for ext in .js .jsx .ts .tsx; do
        [[ -f "$DASH_SRC/plugins/${rel}${ext}" ]] && { resolved="$DASH_SRC/plugins/${rel}${ext}"; break; }
    done
    [[ -n "$resolved" ]] \
        || fail "$t imports '$spec', which does not resolve to a file under $DASH_SRC/plugins/"
done
pass "the registry import resolves at the documented destination (src/plugins/)"

# 3. The symbols it imports must actually be exported by the registry.
registry="$DASH_SRC/plugins/registry.js"
for sym in registerRoutes registerExternalLinks; do
    grep -qE "^export (function|const) $sym\b" "$registry" \
        || fail "registry.js does not export $sym, which the template imports"
done
pass "the template imports only symbols the registry exports"

# 4. The two shipped copies must not drift apart.
cmp -s "${TEMPLATES[0]}" "${TEMPLATES[1]}" \
    || fail "the two template copies have diverged: $(printf '%s ' "${TEMPLATES[@]}")"
pass "both template copies are identical"

# 5. Guard the convention that produced the bug: no .js under the dashboard
#    source tree may contain JSX elements.
offenders=""
while IFS= read -r f; do
    grep -qE '</[A-Za-z][A-Za-z0-9]*>|<[A-Z][A-Za-z0-9]* [a-zA-Z-]+=' "$f" && offenders+="$f "
done < <(find "$DASH_SRC" -name '*.js' -not -path '*/node_modules/*')
[[ -z "$offenders" ]] || fail "JSX found in .js file(s), which vite will not transform: $offenders"
pass "no .js file under the dashboard source contains JSX"
