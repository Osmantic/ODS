#!/bin/bash
# ============================================================================
# ODS — Service Registry Test Suite
# ============================================================================
# Tests the service registry (lib/service-registry.sh), manifest validation,
# and the enable/disable mechanism.
#
# Usage: bash tests/test-service-registry.sh
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
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}SKIP${NC}  $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..60})${NC}"
}

# ============================================
# TEST 1: Registry File Exists and Sources
# ============================================
header "1/7" "Registry Library"

if [[ -f "$PROJECT_DIR/lib/service-registry.sh" ]]; then
    pass "lib/service-registry.sh exists"
else
    fail "lib/service-registry.sh not found"
    echo -e "${RED}Cannot continue without registry library.${NC}"
    exit 1
fi

# Check bash syntax
if bash -n "$PROJECT_DIR/lib/service-registry.sh" 2>/dev/null; then
    pass "lib/service-registry.sh has valid bash syntax"
else
    fail "lib/service-registry.sh has syntax errors"
fi

# Source it and load
export SCRIPT_DIR="$PROJECT_DIR"
. "$PROJECT_DIR/lib/service-registry.sh"

if sr_load 2>/dev/null; then
    pass "sr_load() succeeds"
else
    fail "sr_load() failed"
fi

if [[ ${#SERVICE_IDS[@]} -gt 0 ]]; then
    pass "SERVICE_IDS populated (${#SERVICE_IDS[@]} services)"
else
    fail "SERVICE_IDS is empty — no manifests loaded"
fi

# ============================================
# TEST 2: Manifest Schema Validation
# ============================================
header "2/7" "Manifest Schema Validation"

PYTHON_CMD="python3"
if [[ -f "$PROJECT_DIR/lib/python-cmd.sh" ]]; then
    . "$PROJECT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd_with_module yaml 2>/dev/null || ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if ! "$PYTHON_CMD" -c "import yaml" 2>/dev/null; then
    skip "PyYAML not installed — cannot validate manifests"
else
    manifest_count=0
    for svc_dir in "$PROJECT_DIR"/extensions/services/*/; do
        [[ ! -d "$svc_dir" ]] && continue
        manifest="$svc_dir/manifest.yaml"
        [[ ! -f "$manifest" ]] && continue
        manifest_count=$((manifest_count + 1))
        svc_name="$(basename "$svc_dir")"

        # Validate YAML syntax
        if "$PYTHON_CMD" -c "import sys, yaml; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" "$manifest" 2>/dev/null; then
            pass "Valid YAML: $svc_name/manifest.yaml"
        else
            fail "Invalid YAML: $svc_name/manifest.yaml"
            continue
        fi

        # Validate required fields. host_network services (Docker
        # network_mode: host) don't have a Docker-mapped port and may
        # not serve an HTTP health endpoint — port and health drop to
        # "optional" for them.
        validation=$("$PYTHON_CMD" -c "
import yaml, sys
with open(sys.argv[1]) as f:
    m = yaml.safe_load(f)
errors = []
if m.get('schema_version') != 'ods.services.v1':
    errors.append('missing/wrong schema_version')
s = m.get('service', {})
if not isinstance(s, dict):
    errors.append('service must be a dict')
else:
    host_network = bool(s.get('host_network'))
    required = ['id', 'name'] if host_network else ['id', 'name', 'port', 'health']
    for field in required:
        if not s.get(field):
            errors.append(f'missing required field: service.{field}')
    if 'category' in s and s['category'] not in ('core', 'recommended', 'optional'):
        errors.append(f'invalid category: {s[\"category\"]}')
    if 'gpu_backends' in s:
        for gb in s['gpu_backends']:
            if gb not in ('amd', 'nvidia', 'apple', 'all'):
                errors.append(f'invalid gpu_backend: {gb}')
    if 'aliases' in s and not isinstance(s['aliases'], list):
        errors.append('aliases must be a list')
    if 'depends_on' in s and not isinstance(s['depends_on'], list):
        errors.append('depends_on must be a list')
if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print('OK')
" "$manifest" 2>&1)

        if [[ "$validation" == "OK" ]]; then
            pass "Schema valid: $svc_name"
        else
            fail "Schema invalid: $svc_name" "${validation#FAIL:}"
        fi
    done

    if [[ $manifest_count -eq 0 ]]; then
        fail "No manifest.yaml files found in extensions/services/*/"
    else
        pass "Validated $manifest_count manifests"
    fi
fi

declare -A SERVICE_INTERNAL_PORTS=()
if "$PYTHON_CMD" -c "import yaml" 2>/dev/null; then
    while IFS=$'\t' read -r sid internal_port; do
        [[ -z "$sid" ]] && continue
        SERVICE_INTERNAL_PORTS["$sid"]="$internal_port"
    done < <("$PYTHON_CMD" - "$EXTENSIONS_DIR" <<'PY'
import sys
import yaml
from pathlib import Path

ext_dir = Path(sys.argv[1])
if not ext_dir.exists():
    raise SystemExit(0)

service_dirs = sorted(ext_dir.iterdir())
user_ext_dir = ext_dir.parent.parent / "data" / "user-extensions"
if user_ext_dir.exists():
    service_dirs += sorted(user_ext_dir.iterdir())

seen = set()
for service_dir in service_dirs:
    if not service_dir.is_dir():
        continue
    manifest_path = None
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = service_dir / name
        if candidate.exists():
            manifest_path = candidate
            break
    if manifest_path is None:
        continue
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "ods.services.v1":
        continue
    service = manifest.get("service")
    if not isinstance(service, dict):
        continue
    sid = service.get("id")
    if not sid or sid in seen:
        continue
    seen.add(sid)
    print(f"{sid}\t{service.get('port', 0) or 0}")
PY
    )
fi

# ============================================
# TEST 3: Core Service Manifests
# ============================================
header "3/7" "Core Service Manifests"

expected_core=("llama-server" "open-webui" "dashboard" "dashboard-api")
for sid in "${expected_core[@]}"; do
    manifest="$PROJECT_DIR/extensions/services/$sid/manifest.yaml"
    if [[ -f "$manifest" ]]; then
        pass "Core manifest exists: $sid"
    else
        fail "Core manifest missing: $sid"
        continue
    fi

    # Verify category is "core"
    cat_check=$("$PYTHON_CMD" -c "
import sys, yaml
m = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
print(m.get('service',{}).get('category',''))
" "$manifest" 2>/dev/null || echo "")
    if [[ "$cat_check" == "core" ]]; then
        pass "Category is core: $sid"
    else
        fail "Category is not core: $sid (got: $cat_check)"
    fi
done

# ============================================
# TEST 4: Registry Resolution (Aliases)
# ============================================
header "4/7" "Alias Resolution"

# Test known aliases
declare -A expected_aliases=(
    [llm]="llama-server"
    [webui]="open-webui"
    [ui]="open-webui"
    [web]="open-webui"
    [stt]="whisper"
    [voice]="whisper"
    [workflows]="n8n"
    [search]="searxng"
)

for alias in "${!expected_aliases[@]}"; do
    expected="${expected_aliases[$alias]}"
    resolved=$(sr_resolve "$alias")
    if [[ "$resolved" == "$expected" ]]; then
        pass "Alias '$alias' → '$expected'"
    else
        fail "Alias '$alias' → '$resolved' (expected: '$expected')"
    fi
done

# Identity resolution (service IDs resolve to themselves)
for sid in llama-server open-webui n8n whisper tts; do
    resolved=$(sr_resolve "$sid")
    if [[ "$resolved" == "$sid" ]]; then
        pass "Identity: '$sid' → '$sid'"
    else
        fail "Identity broken: '$sid' → '$resolved'"
    fi
done

# Unknown names pass through unchanged
resolved=$(sr_resolve "nonexistent-service")
if [[ "$resolved" == "nonexistent-service" ]]; then
    pass "Unknown name passes through: 'nonexistent-service'"
else
    fail "Unknown name did not pass through: got '$resolved'"
fi

# ============================================
# TEST 5: Registry Data Completeness
# ============================================
header "5/7" "Registry Data Completeness"

for sid in "${SERVICE_IDS[@]}"; do
    # Every service should have a name
    if [[ -n "${SERVICE_NAMES[$sid]:-}" ]]; then
        pass "Has name: $sid → ${SERVICE_NAMES[$sid]}"
    else
        fail "Missing name: $sid"
    fi

    # Every service should have a category
    cat="${SERVICE_CATEGORIES[$sid]:-}"
    if [[ "$cat" == "core" || "$cat" == "recommended" || "$cat" == "optional" ]]; then
        pass "Valid category: $sid → $cat"
    else
        fail "Invalid/missing category: $sid → '$cat'"
    fi

    # Every service should have a health endpoint
    if [[ -n "${SERVICE_HEALTH[$sid]:-}" ]]; then
        pass "Has health endpoint: $sid → ${SERVICE_HEALTH[$sid]}"
    else
        fail "Missing health endpoint: $sid"
    fi

    # Every service should have a runtime port. SERVICE_PORTS is the
    # user-facing external port; internal-only services may intentionally set
    # it to 0 when a proxy owns the LAN entry point. Host-network services
    # share the host namespace and have no Docker-mapped port to validate.
    if [[ "${SERVICE_HOST_NETWORK[$sid]:-}" == "1" ]]; then
        pass "host_network service exempt from port check: $sid"
    else
        port="${SERVICE_PORTS[$sid]:-0}"
        if [[ "$port" != "0" ]]; then
            pass "Has external port: $sid → $port"
        elif [[ "${SERVICE_INTERNAL_PORTS[$sid]:-0}" != "0" ]]; then
            pass "Internal-only service has container port: $sid → ${SERVICE_INTERNAL_PORTS[$sid]}"
        else
            fail "Missing/zero port: $sid"
        fi
    fi
done

# ============================================
# TEST 6: Compose Fragment Consistency
# ============================================
header "6/7" "Compose Fragments"

for sid in "${SERVICE_IDS[@]}"; do
    cat="${SERVICE_CATEGORIES[$sid]}"
    svc_dir="$PROJECT_DIR/extensions/services/$sid"

    if [[ "$cat" == "core" ]]; then
        # Core services should NOT have compose.yaml (live in base.yml)
        if [[ ! -f "$svc_dir/compose.yaml" ]]; then
            pass "Core service has no compose fragment: $sid"
        else
            # comfyui is an exception — it has a stub compose.yaml
            # Actually, let's just warn — some core services might have compose fragments
            fail "Core service has compose fragment (unexpected): $sid"
        fi
    else
        # Host-native services (e.g. host-systemd) don't need compose fragments
        svc_type=$("$PYTHON_CMD" -c "
import sys, yaml
m = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
print(m.get('service',{}).get('type','docker'))
" "$svc_dir/manifest.yaml" 2>/dev/null || echo "docker")

        if [[ "$svc_type" != "docker" ]]; then
            pass "Non-docker service has no compose fragment (type=$svc_type): $sid"
        elif [[ -f "$svc_dir/compose.yaml" || -f "$svc_dir/compose.yaml.disabled" ]]; then
            pass "Extension has compose fragment: $sid"
        else
            fail "Extension missing compose fragment: $sid"
        fi

        # If compose.yaml exists, validate it
        if [[ -f "$svc_dir/compose.yaml" ]]; then
            if "$PYTHON_CMD" -c "import sys, yaml; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" "$svc_dir/compose.yaml" 2>/dev/null; then
                pass "Valid YAML compose: $sid/compose.yaml"
            else
                fail "Invalid YAML compose: $sid/compose.yaml"
            fi
        fi
    fi
done

# ============================================
# TEST 7: Enable/Disable Mechanism
# ============================================
header "7/7" "Enable/Disable Mechanism"

# Find a non-core service that's currently enabled (has compose.yaml)
test_service=""
for sid in "${SERVICE_IDS[@]}"; do
    cat="${SERVICE_CATEGORIES[$sid]}"
    svc_dir="$PROJECT_DIR/extensions/services/$sid"
    if [[ "$cat" != "core" && -f "$svc_dir/compose.yaml" ]]; then
        test_service="$sid"
        break
    fi
done

if [[ -z "$test_service" ]]; then
    skip "No enabled non-core service found to test disable/enable cycle"
else
    svc_dir="$PROJECT_DIR/extensions/services/$test_service"
    pass "Selected test service: $test_service"

    # Disable: rename compose.yaml → compose.yaml.disabled
    cp "$svc_dir/compose.yaml" "$svc_dir/compose.yaml.backup"
    mv "$svc_dir/compose.yaml" "$svc_dir/compose.yaml.disabled"

    if [[ ! -f "$svc_dir/compose.yaml" && -f "$svc_dir/compose.yaml.disabled" ]]; then
        pass "Disable works: compose.yaml → compose.yaml.disabled"
    else
        fail "Disable failed: files not in expected state"
    fi

    # Verify sr_list_enabled no longer includes it
    _SR_LOADED=false  # Force reload
    sr_load
    enabled_list=$(sr_list_enabled)
    if echo "$enabled_list" | grep -q "^${test_service}$"; then
        fail "Disabled service still appears in sr_list_enabled"
    else
        pass "Disabled service excluded from sr_list_enabled"
    fi

    # Re-enable: rename back
    mv "$svc_dir/compose.yaml.disabled" "$svc_dir/compose.yaml"

    if [[ -f "$svc_dir/compose.yaml" && ! -f "$svc_dir/compose.yaml.disabled" ]]; then
        pass "Enable works: compose.yaml.disabled → compose.yaml"
    else
        fail "Enable failed: files not in expected state"
    fi

    # Verify it's back in sr_list_enabled
    _SR_LOADED=false
    sr_load
    enabled_list=$(sr_list_enabled)
    if echo "$enabled_list" | grep -q "^${test_service}$"; then
        pass "Re-enabled service appears in sr_list_enabled"
    else
        fail "Re-enabled service not in sr_list_enabled"
    fi

    # Clean up backup
    rm -f "$svc_dir/compose.yaml.backup"
    pass "Cleanup complete"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC} ${BOLD}($TOTAL total)${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
