#!/bin/bash
# validate-manifest-schema.sh - Manifest schema validator
# Part of: scripts/
# Purpose: Validate extension manifests against the checked-in JSON Schema.
#
# Usage: ./validate-manifest-schema.sh [--strict] [--verbose]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST_FILE="${ROOT_DIR}/manifest.json"
DEFAULT_MANIFEST_DIRS="${ROOT_DIR}/extensions/services:${ROOT_DIR}/extensions/library/services"
MANIFEST_DIRS="${ODS_MANIFEST_DIRS:-$DEFAULT_MANIFEST_DIRS}"
SCHEMA_PATH=""

STRICT_MODE=false
VERBOSE=false
ERRORS=0
WARNINGS=0

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    cat << EOF
Extension Manifest Schema Validator

Usage: $(basename "$0") [OPTIONS]

OPTIONS:
    -h, --help      Show this help message
    -s, --strict    Treat warnings as errors
    -v, --verbose   Show detailed validation output

DESCRIPTION:
    Validates bundled and library extension manifest.yaml files against the
    schema declared by manifest.json at contracts.extensions.serviceManifestSchema.
    The JSON Schema is the source of truth for manifest validity; this script
    only adds non-blocking operational warnings such as missing compose files.

ENVIRONMENT:
    ODS_MANIFEST_DIRS   Colon-separated manifest directories to validate.
                        Defaults to extensions/services and extensions/library/services.

EXAMPLES:
    $(basename "$0")              # Validate all manifests
    $(basename "$0") --strict     # Fail on warnings
    $(basename "$0") --verbose    # Show all checks
EOF
}

error() {
    echo -e "${RED}✗ ERROR:${NC} $*" >&2
    ((ERRORS++)) || true
}

warn() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*" >&2
    ((WARNINGS++)) || true
}

info() {
    [[ "$VERBOSE" == "true" ]] && echo -e "${BLUE}ℹ${NC} $*"
}

success() {
    [[ "$VERBOSE" == "true" ]] && echo -e "${GREEN}✓${NC} $*"
}

check_python_deps() {
    if ! python3 - <<'PYEOF' >/dev/null 2>&1
import jsonschema  # noqa: F401
import yaml  # noqa: F401
PYEOF
    then
        echo -e "${RED}✗ ERROR:${NC} Manifest schema validation requires Python modules: PyYAML and jsonschema" >&2
        echo "Install the developer/test validation dependencies, for example:" >&2
        echo "  python3 -m pip install PyYAML jsonschema" >&2
        echo "This dependency is only needed for manifest validation / CI checks, not normal ODS runtime." >&2
        exit 1
    fi
}

resolve_schema_path() {
    python3 - "$MANIFEST_FILE" "$ROOT_DIR" <<'PYEOF'
import json
import sys
from pathlib import Path

manifest_file = Path(sys.argv[1])
root_dir = Path(sys.argv[2])
try:
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    schema_rel = manifest["contracts"]["extensions"]["serviceManifestSchema"]
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    print(f"Cannot resolve contracts.extensions.serviceManifestSchema from {manifest_file}: {exc}", file=sys.stderr)
    sys.exit(1)

schema_path = root_dir / schema_rel
if not schema_path.is_file():
    print(f"Declared service manifest schema not found: {schema_rel}", file=sys.stderr)
    sys.exit(1)

print(schema_path)
PYEOF
}

# Validate a single manifest
validate_manifest() {
    local manifest_path="$1"
    local service_name
    service_name=$(basename "$(dirname "$manifest_path")")

    info "Validating: $service_name"

    python3 - "$manifest_path" "$SCHEMA_PATH" "$service_name" "$VERBOSE" <<'PYEOF'
import json
import os
import sys
from pathlib import Path

import yaml
import jsonschema

manifest_path, schema_path, service_name, verbose = sys.argv[1:5]
errors, warnings = [], []

def error(msg):
    errors.append(msg)
    print(f"ERROR: {service_name}: {msg}", file=sys.stderr)

def warn(msg):
    warnings.append(msg)
    print(f"WARNING: {service_name}: {msg}", file=sys.stderr)

def info(msg):
    if verbose == "true":
        print(f"INFO: {service_name}: {msg}")

def path_for(err):
    return ".".join(str(part) for part in err.path) or "<root>"

try:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
except yaml.YAMLError as exc:
    error(f"Invalid YAML syntax: {exc}")
    sys.exit(1)
except OSError as exc:
    error(f"Cannot read manifest: {exc}")
    sys.exit(1)

try:
    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
except Exception as exc:
    error(f"Cannot read JSON schema: {exc}")
    sys.exit(1)

validator_cls = jsonschema.validators.validator_for(schema)
validator_cls.check_schema(schema)
validator = validator_cls(schema)

schema_errors = sorted(validator.iter_errors(manifest), key=lambda err: list(err.path))
for err in schema_errors:
    error(f"{path_for(err)}: {err.message}")

# Non-authoritative operational warnings only. Validity is determined by the
# JSON Schema above, so these warnings must not duplicate schema rules.
if isinstance(manifest, dict):
    service = manifest.get("service")
    if isinstance(service, dict):
        health = service.get("health")
        if isinstance(health, str) and health and not health.startswith("/"):
            warn(f"health should start with '/': {health}")

        compose_file = service.get("compose_file")
        if isinstance(compose_file, str):
            compose_path = os.path.join(os.path.dirname(manifest_path), compose_file)
            if not os.path.exists(compose_path):
                warn(f"compose_file not found: {compose_file}")

if not errors:
    info("JSON schema: OK")

sys.exit(1 if errors else (2 if warnings else 0))
PYEOF

    case $? in
        0) success "$service_name: Valid"; return 0 ;;
        1) ((ERRORS++)) || true; return 1 ;;
        2) ((WARNINGS++)) || true; return 0 ;;
    esac
}

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        -s|--strict) STRICT_MODE=true; shift ;;
        -v|--verbose) VERBOSE=true; shift ;;
        *) echo "Unknown: $1" >&2; usage; exit 2 ;;
    esac
done

check_python_deps
SCHEMA_PATH="$(resolve_schema_path)"

# Main
echo "Validating manifests in: $MANIFEST_DIRS"
echo "Schema: ${SCHEMA_PATH#$ROOT_DIR/}"
echo ""

TOTAL=0 VALID=0
IFS=':' read -r -a MANIFEST_DIR_ARRAY <<< "$MANIFEST_DIRS"
for extensions_dir in "${MANIFEST_DIR_ARRAY[@]}"; do
    [[ -z "$extensions_dir" ]] && continue
    [[ ! -d "$extensions_dir" ]] && { echo -e "${RED}ERROR:${NC} Not found: $extensions_dir" >&2; exit 1; }

    for dir in "$extensions_dir"/*/; do
        [[ ! -d "$dir" ]] && continue
        manifest=""
        for name in manifest.yaml manifest.yml; do
            [[ -f "$dir/$name" ]] && manifest="$dir/$name" && break
        done
        [[ -z "$manifest" ]] && { warn "$(basename "$dir"): No manifest"; continue; }
        ((TOTAL++)) || true
        validate_manifest "$manifest" && { ((VALID++)) || true; }
    done
done

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Summary: $TOTAL total, $VALID valid, $ERRORS errors, $WARNINGS warnings"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ $ERRORS -gt 0 ]]; then
    echo -e "${RED}✗ FAILED${NC} ($ERRORS errors)"; exit 1
elif [[ $WARNINGS -gt 0 && "$STRICT_MODE" == "true" ]]; then
    echo -e "${YELLOW}✗ FAILED${NC} ($WARNINGS warnings in strict mode)"; exit 1
elif [[ $WARNINGS -gt 0 ]]; then
    echo -e "${YELLOW}⚠ Passed with warnings${NC}"; exit 0
else
    echo -e "${GREEN}✓ All valid${NC}"; exit 0
fi
