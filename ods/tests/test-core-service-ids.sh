#!/usr/bin/env bash
# ============================================================================
# core-service-ids.json parity across hardcoded fallbacks
# ----------------------------------------------------------------------------
# config/core-service-ids.json is the allowlist of built-in service names that
# user extensions may not shadow. Three consumers keep a hardcoded copy for
# when the JSON is missing or unparseable:
#   - scripts/resolve-compose-stack.sh  (_CORE_SERVICE_IDS except-branch)
#   - extensions/services/dashboard-api/config.py (_load_core_service_ids)
#   - bin/ods-host-agent.py (_FALLBACK_CORE_IDS)
# A fallback that drifts behind the JSON re-opens the collision guard for the
# missing ids exactly when the JSON file is unavailable.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== core-service-ids fallback parity tests ==="
echo ""

# Ground truth: the shipped allowlist.
json_ids="$(python3 -c 'import json,sys; print("\n".join(sorted(json.load(open(sys.argv[1])))))' \
    "$ROOT_DIR/config/core-service-ids.json")"
json_count="$(printf '%s\n' "$json_ids" | grep -c .)"
if [[ "$json_count" -ge 20 ]]; then
    pass "core-service-ids.json declares $json_count built-in ids"
else
    fail "core-service-ids.json looks truncated (got: $json_count ids)"
fi

# Extract the quoted ids inside one anchored fallback block: from the line
# containing $2 (e.g. "_CORE_SERVICE_IDS = {" or "frozenset({") up to the
# closing "})"/"}", whichever the block uses.
extract_ids() {
    python3 - "$1" "$2" <<'PY'
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
anchor = sys.argv[2]
start = next(i for i, l in enumerate(lines) if anchor in l)
depth = 0
ids = []
for l in lines[start:]:
    depth += l.count("{") - l.count("}")
    ids += re.findall(r'"([a-z0-9-]+)"', l)
    if depth <= 0:
        break
print("\n".join(sorted(set(ids))))
PY
}

# Every hardcoded fallback must cover every id in the JSON.
check_fallback() {
    local file="$1" anchor="$2" label="$3"
    local ids
    ids="$(extract_ids "$file" "$anchor")"
    local missing
    missing="$(LC_ALL=C comm -23 <(printf '%s\n' "$json_ids" | LC_ALL=C sort) <(printf '%s\n' "$ids" | LC_ALL=C sort) || true)"
    if [[ -z "$missing" ]]; then
        pass "$label covers all $json_count core ids"
    else
        fail "$label is missing core ids: $(echo "$missing" | tr '\n' ' ')"
    fi
}

check_fallback "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    "_CORE_SERVICE_IDS = {" "resolve-compose-stack.sh fallback"
check_fallback "$ROOT_DIR/extensions/services/dashboard-api/config.py" \
    "return frozenset({" "dashboard-api config.py fallback"
check_fallback "$ROOT_DIR/bin/ods-host-agent.py" \
    "_FALLBACK_CORE_IDS = frozenset({" "ods-host-agent.py fallback"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
