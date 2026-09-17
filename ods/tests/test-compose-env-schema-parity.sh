#!/usr/bin/env bash
# Every ${KEY} the compose stack reads is a knob a user can set in .env.
# validate-env.sh rejects any .env key the schema does not declare, so a
# compose interpolation with no schema entry turns "tune the documented
# default" into a hard validation failure on the next `ods-cli` run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCHEMA="$PROJECT_DIR/.env.schema.json"

pass_count=0
fail_count=0

pass() { echo "[PASS] $*"; pass_count=$((pass_count + 1)); }
fail() { echo "[FAIL] $*" >&2; fail_count=$((fail_count + 1)); }

[[ -f "$SCHEMA" ]] || { echo "[FAIL] schema not found: $SCHEMA" >&2; exit 1; }

# Compose files that make up the shipped stack. Extension compose files are
# covered by the manifest env-var contract instead.
compose_files=()
while IFS= read -r file; do
    compose_files+=("$file")
done < <(
    find "$PROJECT_DIR" -maxdepth 1 -name 'docker-compose*.yml' -print
    find "$PROJECT_DIR/installers" -name 'docker-compose*.yml' -print 2>/dev/null
)

(( ${#compose_files[@]} > 0 )) || { echo "[FAIL] no compose files found" >&2; exit 1; }

# Interpolated keys, both ${KEY} and ${KEY:-default}. Compose only expands
# upper-case-style env names here; anything else is a literal. `$${KEY}` is an
# escaped literal that compose passes to the container shell untouched, so it
# is not a .env key and must not be collected.
mapfile -t compose_keys < <(
    grep -ho '[^$]\${[A-Z][A-Z0-9_]*' "${compose_files[@]}" \
        | sed 's/^.\${//' \
        | sort -u
)

(( ${#compose_keys[@]} > 0 )) || { echo "[FAIL] no interpolated keys found" >&2; exit 1; }

undeclared=()
for key in "${compose_keys[@]}"; do
    if ! grep -q "\"$key\":" "$SCHEMA"; then
        undeclared+=("$key")
    fi
done

if (( ${#undeclared[@]} == 0 )); then
    pass "all ${#compose_keys[@]} compose-interpolated keys are declared in .env.schema.json"
else
    fail "compose reads keys .env.schema.json does not declare (validate-env.sh will reject them as unknown):"
    for key in "${undeclared[@]}"; do
        echo "         - $key" >&2
    done
fi

# The schema is the validator's whole vocabulary, so a malformed document
# silently degrades every check above it.
if command -v python3 >/dev/null 2>&1; then
    if python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$SCHEMA"; then
        pass ".env.schema.json parses as JSON"
    else
        fail ".env.schema.json is not valid JSON"
    fi
fi

echo ""
echo "Passed: $pass_count  Failed: $fail_count"
(( fail_count == 0 )) || exit 1
