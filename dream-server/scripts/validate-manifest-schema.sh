#!/bin/bash
# validate-manifest-schema.sh - Comprehensive manifest schema validator
# Part of: scripts/
# Purpose: Validate extension manifests against schema requirements
#
# Usage: ./validate-manifest-schema.sh [--strict] [--verbose]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MANIFEST_DIRS="${SCRIPT_DIR}/../extensions/services:${SCRIPT_DIR}/../extensions/library/services"
MANIFEST_DIRS="${DREAM_MANIFEST_DIRS:-$DEFAULT_MANIFEST_DIRS}"

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
    Validates bundled and library extension manifest.yaml files against schema requirements.
    Checks required fields, types, formats, and logical consistency.

ENVIRONMENT:
    DREAM_MANIFEST_DIRS   Colon-separated manifest directories to validate.
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

def has_text(value):
    return isinstance(value, str) and len(value) > 0

def is_int(value):
    return type(value) is int

try:
    manifest = yaml.safe_load(open(manifest_path))
    if not isinstance(manifest, dict): error("Not a valid YAML mapping"); sys.exit(1)

    # schema_version
    if manifest.get("schema_version") != "dream.services.v1":
        error(f"Invalid schema_version: {manifest.get('schema_version')}")
    else: info("schema_version: OK")

    service = manifest.get("service", {})
    if not isinstance(service, dict): error("Missing/invalid 'service' section"); sys.exit(1)

    # Required fields. host_network services use compose/native health checks and
    # do not expose a Docker-mapped HTTP health path.
    host_network = service.get("host_network", False)
    if "host_network" in service and not isinstance(host_network, bool):
        error("Invalid type for service.host_network")

    required_fields = {"id": str, "name": str, "port": int, "type": str, "category": str}
    if host_network is not True:
        required_fields["health"] = str

    for field, typ in required_fields.items():
        val = service.get(field)
        if val is None: error(f"Missing service.{field}")
        elif typ is int and not is_int(val): error(f"Invalid type for service.{field}")
        elif typ is not int and not isinstance(val, typ): error(f"Invalid type for service.{field}")
        else: info(f"service.{field}: OK")

    # Validate formats
    if service.get("id") and not re.match(r'^[a-z0-9][a-z0-9-]*$', service["id"]):
        error(f"Invalid service.id format: {service['id']}")
    if "name" in service and not has_text(service.get("name")):
        error("Invalid service.name")
    if service.get("category") not in ["core", "recommended", "optional", None]:
        error(f"Invalid category: {service.get('category')}")
    if service.get("type") not in ["docker", "host-systemd", None]:
        error(f"Invalid type: {service.get('type')}")
    
    port = service.get("port", 0)
    if is_int(port) and not (0 <= port <= 65535):
        error(f"Invalid port: {port}")

    if service.get("health") and not service["health"].startswith("/"):
        warn(f"health should start with '/': {service['health']}")

    # Validate lists
    for alias in service.get("aliases", []):
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', str(alias)):
            error(f"Invalid alias: {alias}")

    for dep in service.get("depends_on", []):
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', str(dep)):
            error(f"Invalid dependency: {dep}")

    for backend in service.get("gpu_backends", []):
        if backend not in ["amd", "nvidia", "apple", "cpu", "none", "all"]:
            error(f"Invalid gpu_backend: {backend}")
    if "gpu_backends" in service and not service.get("gpu_backends"):
        error("service.gpu_backends must not be empty")

    for env_var in service.get("env_vars", []):
        if not isinstance(env_var, dict):
            error("Invalid env_vars entry")
            continue
        if "key" not in env_var:
            error("env_vars entry missing key")
        elif not isinstance(env_var["key"], str):
            error("Invalid env_vars key")
        for bool_field in ["required", "secret"]:
            if bool_field in env_var and not isinstance(env_var[bool_field], bool):
                error(f"Invalid env_vars {bool_field}")
        for str_field in ["description", "default"]:
            if str_field in env_var and not isinstance(env_var[str_field], str):
                error(f"Invalid env_vars {str_field}")
        extra_keys = set(env_var) - {"key", "required", "secret", "description", "default"}
        for key in sorted(extra_keys):
            error(f"Invalid env_vars property: {key}")

    for feature in manifest.get("features", []) or []:
        if not isinstance(feature, dict):
            error("Invalid feature entry")
            continue
        for field in ["id", "name", "description", "icon", "category", "requirements", "priority"]:
            if field not in feature:
                error(f"Missing feature.{field}")
        if feature.get("id") and not re.match(r'^[a-z0-9][a-z0-9-]*$', str(feature["id"])):
            error(f"Invalid feature.id format: {feature['id']}")
        for field in ["name", "description", "icon", "category"]:
            if field in feature and not has_text(feature.get(field)):
                error(f"Invalid feature.{field}")
        if "requirements" in feature and not isinstance(feature.get("requirements"), dict):
            error("Invalid feature.requirements")
        priority = feature.get("priority")
        if priority is not None and (not is_int(priority) or priority < 1):
            error(f"Invalid feature.priority: {priority}")
        for backend in feature.get("gpu_backends", []):
            if backend not in ["amd", "nvidia", "apple", "cpu", "none", "all"]:
                error(f"Invalid feature gpu_backend: {backend}")

    for tag in manifest.get("tags", []) or []:
        if not isinstance(tag, str) or not re.match(r'^[a-z0-9][a-z0-9-]*$', tag):
            error(f"Invalid tag: {tag}")

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
echo "Validating manifests in: $MANIFEST_DIRS"
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
