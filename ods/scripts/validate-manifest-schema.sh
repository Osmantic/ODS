#!/bin/bash
# validate-manifest-schema.sh - Comprehensive manifest schema validator
# Part of: scripts/
# Purpose: Validate extension manifests against schema requirements
#
# Usage: ./validate-manifest-schema.sh [--strict] [--verbose]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSIONS_DIR="${SCRIPT_DIR}/../extensions/services"

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
    Validates all extension manifest.yaml files against schema requirements.
    Checks required fields, types, formats, and logical consistency.

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

# Validate a single manifest
validate_manifest() {
    local manifest_path="$1"
    local service_name
    service_name=$(basename "$(dirname "$manifest_path")")

    info "Validating: $service_name"

    # Check YAML syntax
    if ! python3 -c "import yaml; yaml.safe_load(open('$manifest_path'))" 2>/dev/null; then
        error "$service_name: Invalid YAML syntax"
        return 1
    fi

    # Comprehensive validation
    python3 - "$manifest_path" "$service_name" "$VERBOSE" <<'PYEOF'
import yaml, sys, re, os

manifest_path, service_name, verbose = sys.argv[1:4]
errors, warnings = [], []

def error(msg): errors.append(msg); print(f"ERROR: {service_name}: {msg}", file=sys.stderr)
def warn(msg): warnings.append(msg); print(f"WARNING: {service_name}: {msg}", file=sys.stderr)
def info(msg): verbose == "true" and print(f"INFO: {service_name}: {msg}")

try:
    manifest = yaml.safe_load(open(manifest_path))
    if not isinstance(manifest, dict): error("Not a valid YAML mapping"); sys.exit(1)

    # schema_version
    if manifest.get("schema_version") != "ods.services.v1":
        error(f"Invalid schema_version: {manifest.get('schema_version')}")
    else: info("schema_version: OK")

    service = manifest.get("service", {})
    if not isinstance(service, dict): error("Missing/invalid 'service' section"); sys.exit(1)

    # Required fields
    for field, typ in {"id": str, "name": str, "port": int, "health": str, "type": str, "category": str}.items():
        val = service.get(field)
        if val is None: error(f"Missing service.{field}")
        elif not isinstance(val, typ): error(f"Invalid type for service.{field}")
        else: info(f"service.{field}: OK")

    # Validate formats
    if service.get("id") and not re.match(r'^[a-z0-9_-]+$', service["id"]):
        error(f"Invalid service.id format: {service['id']}")
    if service.get("category") not in ["core", "recommended", "optional", None]:
        error(f"Invalid category: {service.get('category')}")
    if service.get("type") not in ["docker", "native", "external", None]:
        error(f"Invalid type: {service.get('type')}")
    
    port = service.get("port", 0)
    if isinstance(port, int) and not (0 <= port <= 65535):
        error(f"Invalid port: {port}")

    if service.get("health") and not service["health"].startswith("/"):
        warn(f"health should start with '/': {service['health']}")

    # Validate lists
    for alias in service.get("aliases", []):
        if not re.match(r'^[a-z0-9_-]+$', str(alias)):
            error(f"Invalid alias: {alias}")

    for dep in service.get("depends_on", []):
        if not re.match(r'^[a-z0-9_-]+$', str(dep)):
            error(f"Invalid dependency: {dep}")

    for backend in service.get("gpu_backends", []):
        if backend not in ["amd", "nvidia", "apple", "cpu", "all"]:
            error(f"Invalid gpu_backend: {backend}")

    # Check compose_file exists
    if service.get("compose_file"):
        compose_path = os.path.join(os.path.dirname(manifest_path), service["compose_file"])
        if not os.path.exists(compose_path):
            warn(f"compose_file not found: {service['compose_file']}")

    sys.exit(1 if errors else (2 if warnings else 0))
except Exception as e:
    print(f"ERROR: {service_name}: {e}", file=sys.stderr); sys.exit(1)
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

# Main
echo "Validating manifests in: $EXTENSIONS_DIR"
echo ""

[[ ! -d "$EXTENSIONS_DIR" ]] && { echo -e "${RED}ERROR:${NC} Not found: $EXTENSIONS_DIR" >&2; exit 1; }

TOTAL=0 VALID=0
for dir in "$EXTENSIONS_DIR"/*/; do
    [[ ! -d "$dir" ]] && continue
    manifest=""
    for name in manifest.yaml manifest.yml; do
        [[ -f "$dir/$name" ]] && manifest="$dir/$name" && break
    done
    [[ -z "$manifest" ]] && { warn "$(basename "$dir"): No manifest"; continue; }
    ((TOTAL++)) || true
    validate_manifest "$manifest" && { ((VALID++)) || true; }
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
