#!/usr/bin/env bash
# Test suite: Service templates validation
# Verifies template YAML files parse correctly, reference valid service IDs,
# and have unique template IDs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$PROJECT_DIR/templates"
EXTENSIONS_DIR="$PROJECT_DIR/extensions/services"
# Library extension source (pre-install). Runtime path after install is
# data/extensions-library/ but the source tree ships them under dream-server/extensions/library/.
EXTENSIONS_LIBRARY_DIR="${EXTENSIONS_LIBRARY_DIR:-$PROJECT_DIR/extensions/library/services}"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1" >&2; }

echo "=== Template Validation Tests ==="

# --- Test 1: All template YAML files parse correctly ---
echo ""
echo "--- Test: Template YAML parsing ---"
if [[ ! -d "$TEMPLATES_DIR" ]]; then
    fail "Templates directory not found: $TEMPLATES_DIR"
else
    for f in "$TEMPLATES_DIR"/*.yaml "$TEMPLATES_DIR"/*.yml; do
        [[ -f "$f" ]] || continue
        basename="$(basename "$f")"
        if python3 -c "
import yaml, sys
with open(sys.argv[1]) as fh:
    d = yaml.safe_load(fh)
assert isinstance(d, dict), 'root must be a mapping'
assert d.get('schema_version') == 'dream.templates.v1', 'wrong schema_version'
t = d.get('template', {})
assert t.get('id'), 'missing template.id'
assert t.get('name'), 'missing template.name'
assert isinstance(t.get('services', []), list) and len(t['services']) > 0, 'services must be non-empty list'
" "$f" 2>/dev/null; then
            pass "$basename parses correctly"
        else
            fail "$basename failed to parse"
        fi
    done
fi

# --- Test 2: All template service IDs exist in manifest registry ---
echo ""
echo "--- Test: Template service IDs exist in manifests ---"

# Build set of known service IDs from built-in manifests AND the library.
# Library extensions are installable on demand by the template apply flow,
# so they are valid template service IDs even though they're not under
# extensions/services/.
known_ids=$(python3 - "$EXTENSIONS_DIR" "$EXTENSIONS_LIBRARY_DIR" <<'PYEOF'
import yaml, sys
from pathlib import Path
ids = set()
for root in sys.argv[1:]:
    rp = Path(root)
    if not rp.is_dir():
        continue
    for d in sorted(rp.iterdir()):
        if not d.is_dir():
            continue
        for name in ("manifest.yaml", "manifest.yml"):
            mp = d / name
            if mp.exists():
                try:
                    with open(mp) as f:
                        m = yaml.safe_load(f)
                except Exception:
                    break
                if isinstance(m, dict) and m.get("schema_version") == "dream.services.v1":
                    s = m.get("service", {})
                    if s.get("id"):
                        ids.add(s["id"])
                break
for sid in sorted(ids):
    print(sid)
PYEOF
)

for f in "$TEMPLATES_DIR"/*.yaml "$TEMPLATES_DIR"/*.yml; do
    [[ -f "$f" ]] || continue
    basename="$(basename "$f")"
    services=$(python3 -c "
import yaml, sys
with open(sys.argv[1]) as fh:
    d = yaml.safe_load(fh)
for s in d.get('template', {}).get('services', []):
    print(s)
" "$f" 2>/dev/null) || continue

    all_found=true
    while IFS= read -r svc; do
        [[ -z "$svc" ]] && continue
        if ! echo "$known_ids" | grep -q "^${svc}$"; then
            fail "$basename: service '$svc' not found in manifests"
            all_found=false
        fi
    done <<< "$services"

    if $all_found; then
        pass "$basename: all service IDs exist"
    fi
done

# --- Test 3: Template IDs are unique ---
echo ""
echo "--- Test: Template IDs are unique ---"

all_ids=$(python3 - "$TEMPLATES_DIR" <<'PYEOF'
import yaml, sys
from pathlib import Path
d = Path(sys.argv[1])
for f in sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml"))):
    with open(f) as fh:
        data = yaml.safe_load(fh)
    if isinstance(data, dict) and data.get("schema_version") == "dream.templates.v1":
        tid = data.get("template", {}).get("id", "")
        if tid:
            print(tid)
PYEOF
)

total=$(echo "$all_ids" | wc -l | tr -d ' ')
unique=$(echo "$all_ids" | sort -u | wc -l | tr -d ' ')

if [[ "$total" == "$unique" ]]; then
    pass "All $total template IDs are unique"
else
    fail "Duplicate template IDs found ($total total, $unique unique)"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]] || exit 1
