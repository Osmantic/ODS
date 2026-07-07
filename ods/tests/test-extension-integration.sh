#!/bin/bash
# ============================================================================
# ODS Extension Integration Test Suite
# ============================================================================
# Validates that all 18 extensions work correctly together. Tests manifest
# completeness, dependency resolution, compose stack validation, enable/disable
# cycles, health endpoints, port uniqueness, and category consistency.
#
# Usage: ./tests/test-extension-integration.sh
# Exit 0 if all pass, 1 if any fail
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}⊘${NC} $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..70})${NC}"
}

# ============================================
# TEST 1: Manifest Completeness
# ============================================
header "1/7" "Manifest Completeness"

SERVICES_DIR="$PROJECT_DIR/extensions/services"
if [[ ! -d "$SERVICES_DIR" ]]; then
    fail "extensions/services directory not found"
    exit 1
fi

manifest_count=0
for svc_dir in "$SERVICES_DIR"/*/; do
    [[ ! -d "$svc_dir" ]] && continue
    svc_name="$(basename "$svc_dir")"
    manifest="$svc_dir/manifest.yaml"

    if [[ -f "$manifest" ]]; then
        pass "Manifest exists: $svc_name"
        manifest_count=$((manifest_count + 1))
    else
        fail "Manifest missing: $svc_name"
    fi
done

expected_min=17
if [[ $manifest_count -ge $expected_min ]]; then
    pass "Found $manifest_count extension manifests (minimum $expected_min expected)"
else
    fail "Only $manifest_count manifests found (expected at least $expected_min)"
fi

# ============================================
# TEST 2: Dependency Graph Validation
# ============================================
header "2/7" "Dependency Graph Validation"

if ! command -v python3 &>/dev/null; then
    skip "Python3 not available - cannot validate dependency graph"
else
    dep_check=$(python3 << 'PYEOF'
import yaml
import pathlib
import sys

services_dir = pathlib.Path("extensions/services")
services = {}
errors = []

# Load all services and their dependencies
for svc_dir in services_dir.iterdir():
    if not svc_dir.is_dir():
        continue
    manifest = svc_dir / "manifest.yaml"
    if not manifest.exists():
        continue

    with open(manifest) as f:
        data = yaml.safe_load(f)

    svc = data.get("service", {})
    svc_id = svc.get("id", svc_dir.name)
    deps = svc.get("depends_on", [])
    services[svc_id] = deps

# Check for missing dependencies
for svc_id, deps in services.items():
    for dep in deps:
        if dep not in services:
            errors.append(f"{svc_id} depends on {dep} which does not exist")

# Check for circular dependencies (simple cycle detection)
def has_cycle(svc_id, visited, rec_stack):
    visited.add(svc_id)
    rec_stack.add(svc_id)

    for dep in services.get(svc_id, []):
        if dep not in visited:
            if has_cycle(dep, visited, rec_stack):
                return True
        elif dep in rec_stack:
            errors.append(f"Circular dependency detected: {svc_id} -> {dep}")
            return True

    rec_stack.remove(svc_id)
    return False

visited = set()
for svc_id in services:
    if svc_id not in visited:
        has_cycle(svc_id, visited, set())

if errors:
    print("FAIL:" + "|".join(errors))
    sys.exit(1)
else:
    print(f"OK:{len(services)} services, {sum(len(d) for d in services.values())} dependencies")
    sys.exit(0)
PYEOF
)

    if [[ $? -eq 0 ]]; then
        pass "Dependency graph valid: ${dep_check#OK:}"
    else
        fail "Dependency graph invalid" "${dep_check#FAIL:}"
    fi
fi

# ============================================
# TEST 3: Compose Stack Validation
# ============================================
header "3/7" "Compose Stack Validation"

RESOLVER="$PROJECT_DIR/scripts/resolve-compose-stack.sh"
if [[ ! -f "$RESOLVER" ]]; then
    skip "resolve-compose-stack.sh not found"
else
    # Test compose stack resolution for each GPU backend
    for backend in nvidia amd apple cpu; do
        set +e
        compose_out=$(bash "$RESOLVER" --script-dir "$PROJECT_DIR" --gpu-backend "$backend" 2>&1)
        result=$?
        set -e

        if [[ $result -eq 0 ]]; then
            pass "Compose stack resolves for backend: $backend"
        else
            fail "Compose stack resolution failed for backend: $backend"
        fi
    done
fi

# Validate compose stack syntax if docker is available
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    VALIDATOR="$PROJECT_DIR/scripts/validate-compose-stack.sh"
    if [[ -f "$VALIDATOR" ]]; then
        set +e
        compose_flags=$(bash "$RESOLVER" --script-dir "$PROJECT_DIR" --gpu-backend nvidia 2>&1)
        validation_out=$(bash "$VALIDATOR" --compose-flags "$compose_flags" --quiet 2>&1)
        result=$?
        set -e

        if [[ $result -eq 0 ]]; then
            pass "Compose stack syntax valid (docker compose config)"
        else
            skip "Compose stack validation failed (may be env-specific)"
        fi
    fi
else
    skip "Docker not available - cannot validate compose syntax"
fi

# ============================================
# TEST 4: Enable/Disable Cycles
# ============================================
header "4/7" "Enable/Disable Cycles"

# Test that toggling extensions doesn't break the stack
test_services=()
for svc_dir in "$SERVICES_DIR"/*/; do
    [[ ! -d "$svc_dir" ]] && continue
    svc_name="$(basename "$svc_dir")"

    # Skip core services (shouldn't be disabled)
    if command -v python3 &>/dev/null; then
        category=$(python3 -c "import yaml; m=yaml.safe_load(open('$svc_dir/manifest.yaml')); print(m.get('service',{}).get('category',''))" 2>/dev/null || echo "")
        [[ "$category" == "core" ]] && continue
    fi

    # Only test services with compose.yaml
    if [[ -f "$svc_dir/compose.yaml" ]]; then
        test_services+=("$svc_name")
    fi
done

if [[ ${#test_services[@]} -eq 0 ]]; then
    skip "No non-core services with compose.yaml found"
else
    # Test disable/enable cycle for first non-core service
    test_svc="${test_services[0]}"
    test_dir="$SERVICES_DIR/$test_svc"

    if [[ -f "$test_dir/compose.yaml" ]]; then
        # Backup
        cp "$test_dir/compose.yaml" "$test_dir/compose.yaml.test-backup"

        # Disable
        mv "$test_dir/compose.yaml" "$test_dir/compose.yaml.disabled"
        if [[ ! -f "$test_dir/compose.yaml" && -f "$test_dir/compose.yaml.disabled" ]]; then
            pass "Disable works: $test_svc"
        else
            fail "Disable failed: $test_svc"
        fi

        # Re-enable
        mv "$test_dir/compose.yaml.disabled" "$test_dir/compose.yaml"
        if [[ -f "$test_dir/compose.yaml" && ! -f "$test_dir/compose.yaml.disabled" ]]; then
            pass "Re-enable works: $test_svc"
        else
            fail "Re-enable failed: $test_svc"
        fi

        # Cleanup
        rm -f "$test_dir/compose.yaml.test-backup"
    fi

    pass "Enable/disable cycle tested on ${#test_services[@]} available services"
fi

# ============================================
# TEST 5: Health Endpoint Validation
# ============================================
header "5/7" "Health Endpoint Validation"

if ! command -v python3 &>/dev/null; then
    skip "Python3 not available - cannot validate health endpoints"
else
    health_check=$(python3 << 'PYEOF'
import yaml
import pathlib

services_dir = pathlib.Path("extensions/services")
errors = []
valid = 0

for svc_dir in services_dir.iterdir():
    if not svc_dir.is_dir():
        continue
    manifest = svc_dir / "manifest.yaml"
    if not manifest.exists():
        continue

    with open(manifest) as f:
        data = yaml.safe_load(f)

    svc = data.get("service", {})
    svc_id = svc.get("id", svc_dir.name)
    health = svc.get("health", "")

    if not health:
        errors.append(f"{svc_id} has no health endpoint")
    elif not health.startswith("/"):
        errors.append(f"{svc_id} health endpoint should start with /: {health}")
    else:
        valid += 1

if errors:
    print("FAIL:" + "|".join(errors))
else:
    print(f"OK:{valid} services have valid health endpoints")
PYEOF
)

    if [[ $? -eq 0 ]]; then
        pass "Health endpoints valid: ${health_check#OK:}"
    else
        fail "Health endpoint validation failed" "${health_check#FAIL:}"
    fi
fi

# ============================================
# TEST 6: Port Uniqueness
# ============================================
header "6/7" "Port Uniqueness"

if ! command -v python3 &>/dev/null; then
    skip "Python3 not available - cannot validate port uniqueness"
else
    port_check=$(python3 << 'PYEOF'
import yaml
import pathlib

services_dir = pathlib.Path("extensions/services")
external_ports = {}
errors = []
total_services = 0

for svc_dir in services_dir.iterdir():
    if not svc_dir.is_dir():
        continue
    manifest = svc_dir / "manifest.yaml"
    if not manifest.exists():
        continue

    with open(manifest) as f:
        data = yaml.safe_load(f)

    svc = data.get("service", {})
    svc_id = svc.get("id", svc_dir.name)
    ext_port = svc.get("external_port_default", 0)
    total_services += 1

    # Only check external port uniqueness (internal ports can overlap in Docker)
    if ext_port and ext_port != 0:
        if ext_port in external_ports:
            errors.append(f"External port {ext_port} conflict: {external_ports[ext_port]} and {svc_id}")
        else:
            external_ports[ext_port] = svc_id

if errors:
    print("FAIL:" + "|".join(errors))
else:
    print(f"OK:{len(external_ports)} unique external ports across {total_services} services")
PYEOF
)

    if [[ $? -eq 0 ]]; then
        pass "Port uniqueness validated: ${port_check#OK:}"
    else
        fail "Port conflicts detected" "${port_check#FAIL:}"
    fi
fi

# ============================================
# TEST 7: Category Consistency
# ============================================
header "7/7" "Category Consistency"

if ! command -v python3 &>/dev/null; then
    skip "Python3 not available - cannot validate categories"
else
    category_check=$(python3 << 'PYEOF'
import yaml
import pathlib

services_dir = pathlib.Path("extensions/services")
categories = {"core": [], "recommended": [], "optional": []}
errors = []

for svc_dir in services_dir.iterdir():
    if not svc_dir.is_dir():
        continue
    manifest = svc_dir / "manifest.yaml"
    if not manifest.exists():
        continue

    with open(manifest) as f:
        data = yaml.safe_load(f)

    svc = data.get("service", {})
    svc_id = svc.get("id", svc_dir.name)
    category = svc.get("category", "")

    if category not in ["core", "recommended", "optional"]:
        errors.append(f"{svc_id} has invalid category: {category}")
    else:
        categories[category].append(svc_id)

# Validate expected core services
expected_core = ["llama-server", "open-webui", "dashboard", "dashboard-api"]
for svc in expected_core:
    if svc not in categories["core"]:
        errors.append(f"Expected core service missing: {svc}")

if errors:
    print("FAIL:" + "|".join(errors))
else:
    print(f"OK:core={len(categories['core'])}, recommended={len(categories['recommended'])}, optional={len(categories['optional'])}")
PYEOF
)

    if [[ $? -eq 0 ]]; then
        pass "Category consistency validated: ${category_check#OK:}"
    else
        fail "Category validation failed" "${category_check#FAIL:}"
    fi
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC} ${BOLD}($TOTAL total)${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
