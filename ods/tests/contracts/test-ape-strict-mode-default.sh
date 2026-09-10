#!/usr/bin/env bash
set -euo pipefail

# Regression contract for issue #4170: APE must ship with STRICT_MODE=true by default.
# Fails when compose/.env.example/.env.schema/main.py default to advisory-only mode.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

COMPOSE="extensions/services/ape/compose.yaml"
MAIN="extensions/services/ape/main.py"
ENV_EXAMPLE=".env.example"
ENV_SCHEMA=".env.schema.json"

echo "[contract] APE compose default for APE_STRICT_MODE"
if grep -q 'APE_STRICT_MODE=${APE_STRICT_MODE:-true}' "$COMPOSE"; then
    pass "compose.yaml: APE_STRICT_MODE defaults to true"
else
    fail "compose.yaml: expected APE_STRICT_MODE=\${APE_STRICT_MODE:-true}"
fi

if grep -q 'APE_STRICT_MODE=${APE_STRICT_MODE:-false}' "$COMPOSE"; then
    fail "compose.yaml: must not default APE_STRICT_MODE to false"
fi

echo "[contract] main.py import-time default"
if grep -q 'os.environ.get("APE_STRICT_MODE", "true")' "$MAIN"; then
    pass "main.py: APE_STRICT_MODE defaults to true at import"
else
    fail 'main.py: expected os.environ.get("APE_STRICT_MODE", "true")'
fi

if grep -q 'os.environ.get("APE_STRICT_MODE", "false")' "$MAIN"; then
    fail "main.py: must not default APE_STRICT_MODE to false"
fi

echo "[contract] .env.example documents secure default"
if grep -qE '# APE_STRICT_MODE=true' "$ENV_EXAMPLE"; then
    pass ".env.example: documents APE_STRICT_MODE=true"
else
    fail ".env.example: expected commented APE_STRICT_MODE=true"
fi

if grep -qE '# APE_STRICT_MODE=false' "$ENV_EXAMPLE"; then
    fail ".env.example: must not document APE_STRICT_MODE=false as default"
fi

echo "[contract] .env.schema.json default"
schema_default="$(python3 -c "
import json
from pathlib import Path
schema = json.loads(Path('$ENV_SCHEMA').read_text(encoding='utf-8'))
print(schema['properties']['APE_STRICT_MODE']['default'])
")"
if [[ "$schema_default" == "true" ]]; then
    pass ".env.schema.json: APE_STRICT_MODE default is true"
else
    fail ".env.schema.json: APE_STRICT_MODE default must be true (got: $schema_default)"
fi

echo ""
echo "ALL APE STRICT MODE DEFAULT CONTRACT TESTS PASSED"
