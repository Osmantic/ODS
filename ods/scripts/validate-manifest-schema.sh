#!/bin/bash
# validate-manifest-schema.sh - Manifest schema validator
# Part of: scripts/
# Purpose: Validate extension manifests against the canonical JSON Schema.
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
    Validates bundled and library extension manifests against the schema
    declared by manifest.json at contracts.extensions.serviceManifestSchema.
    JSON Schema is the single source of truth for manifest validity.

ENVIRONMENT:
    ODS_MANIFEST_DIRS   Colon-separated manifest directories to validate.
                        Defaults to bundled and library service directories.

EXAMPLES:
    $(basename "$0")              # Validate all manifests
    $(basename "$0") --strict     # Fail on warnings
    $(basename "$0") --verbose    # Show all checks
EOF
}

error() {
    echo -e "${RED}✗ ERROR:${NC} $*" >&2
    ERRORS=$((ERRORS + 1))
}

warn() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*" >&2
    WARNINGS=$((WARNINGS + 1))
}

info() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "${BLUE}ℹ${NC} $*"
    fi
}

success() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "${GREEN}✓${NC} $*"
    fi
}

check_python_deps() {
    if ! python3 - <<'PYEOF' >/dev/null 2>&1
import jsonschema  # noqa: F401
import yaml  # noqa: F401
PYEOF
    then
        echo "ERROR: Manifest schema validation requires Python modules: PyYAML and jsonschema" >&2
        echo "Install them with: python3 -m pip install PyYAML jsonschema" >&2
        echo "These are developer/CI validation dependencies, not runtime dependencies." >&2
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
    print(
        "Cannot resolve contracts.extensions.serviceManifestSchema "
        f"from {manifest_file}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)

schema_path = root_dir / schema_rel
if not schema_path.is_file():
    print(f"Declared service manifest schema not found: {schema_rel}", file=sys.stderr)
    raise SystemExit(1)

print(schema_path)
PYEOF
}

# Validate all manifests in one Python process so jsonschema and the canonical
# schema are loaded once, even for a full bundled + library catalog scan.
validate_manifests() {
    local results_file="$1"
    shift

    python3 - "$SCHEMA_PATH" "$VERBOSE" "$results_file" "$@" <<'PYEOF'
import json
import os
import sys
from pathlib import Path

import jsonschema
import yaml

schema_path, verbose, results_path, *manifest_paths = sys.argv[1:]

try:
    with open(schema_path, encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
except (OSError, json.JSONDecodeError, jsonschema.exceptions.SchemaError) as exc:
    print(f"ERROR: Cannot load JSON schema: {exc}", file=sys.stderr)
    raise SystemExit(1)

validator = validator_cls(schema)
with open(results_path, "w", encoding="utf-8") as results_file:
    for manifest_path in manifest_paths:
        service_name = Path(manifest_path).parent.name
        errors = []
        warnings = []

        def report_error(message):
            errors.append(message)
            print(f"ERROR: {service_name}: {message}", file=sys.stderr)

        def report_warning(message):
            warnings.append(message)
            print(f"WARNING: {service_name}: {message}", file=sys.stderr)

        try:
            with open(manifest_path, encoding="utf-8") as manifest_file:
                manifest = yaml.safe_load(manifest_file)
        except yaml.YAMLError as exc:
            report_error(f"Invalid YAML syntax: {exc}")
            manifest = None
        except OSError as exc:
            report_error(f"Cannot read manifest: {exc}")
            manifest = None

        if not errors:
            schema_errors = sorted(
                validator.iter_errors(manifest),
                key=lambda validation_error: [
                    str(part) for part in validation_error.path
                ],
            )
            for validation_error in schema_errors:
                error_path = ".".join(
                    str(part) for part in validation_error.path
                ) or "<root>"
                report_error(f"{error_path}: {validation_error.message}")

        # Operational warnings are non-authoritative. Structural and type
        # validity belongs exclusively to the JSON Schema above.
        if isinstance(manifest, dict):
            service = manifest.get("service")
            if isinstance(service, dict):
                health = service.get("health")
                if (
                    isinstance(health, str)
                    and health
                    and not health.startswith("/")
                ):
                    report_warning(f"health should start with '/': {health}")

                compose_file = service.get("compose_file")
                if isinstance(compose_file, str):
                    compose_path = os.path.join(
                        os.path.dirname(manifest_path), compose_file
                    )
                    if not os.path.exists(compose_path):
                        report_warning(f"compose_file not found: {compose_file}")

        if verbose == "true" and not errors:
            print(f"INFO: {service_name}: JSON schema: OK")

        status = "error" if errors else ("warning" if warnings else "valid")
        print(f"{status}\t{service_name}", file=results_file)
PYEOF
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
echo "Schema: ${SCHEMA_PATH#"$ROOT_DIR/"}"
echo ""

TOTAL=0 VALID=0
MANIFEST_PATHS=()
IFS=':' read -r -a MANIFEST_DIR_ARRAY <<< "$MANIFEST_DIRS"
for extensions_dir in "${MANIFEST_DIR_ARRAY[@]}"; do
    [[ -z "$extensions_dir" ]] && continue
    if [[ ! -d "$extensions_dir" ]]; then
        echo -e "${RED}ERROR:${NC} Not found: $extensions_dir" >&2
        exit 1
    fi

    for dir in "$extensions_dir"/*/; do
        [[ ! -d "$dir" ]] && continue
        manifest=""
        for name in manifest.yaml manifest.yml; do
            [[ -f "$dir/$name" ]] && manifest="$dir/$name" && break
        done
        [[ -z "$manifest" ]] && { warn "$(basename "$dir"): No manifest"; continue; }
        TOTAL=$((TOTAL + 1))
        MANIFEST_PATHS+=("$manifest")
    done
done

RESULTS_FILE="$(mktemp)"
trap 'rm -f "$RESULTS_FILE"' EXIT
if ! validate_manifests "$RESULTS_FILE" "${MANIFEST_PATHS[@]}"; then
    exit 1
fi
while IFS=$'\t' read -r status service_name; do
    case "$status" in
        valid)
            VALID=$((VALID + 1))
            success "$service_name: Valid"
            ;;
        warning)
            VALID=$((VALID + 1))
            WARNINGS=$((WARNINGS + 1))
            ;;
        error)
            ERRORS=$((ERRORS + 1))
            ;;
        *)
            error "Invalid validator result for $service_name: $status"
            ;;
    esac
done < "$RESULTS_FILE"

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
