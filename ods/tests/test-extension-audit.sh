#!/bin/bash
# ============================================================================
# ODS — Extension Audit Test Suite
# ============================================================================
# Exercises scripts/audit-extensions.py against controlled fixture projects.
#
# Usage: bash tests/test-extension-audit.sh
# Exit 0 if all pass, 1 if any fail
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AUDIT_SCRIPT="$PROJECT_DIR/scripts/audit-extensions.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0

pass() {
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1/7]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..60})${NC}"
}

make_fixture_root() {
    local root
    root=$(mktemp -d)
    mkdir -p "$root/extensions/services"
    echo "$root"
}

write_service() {
    local root="$1"
    local service_id="$2"
    shift 2
    local dir="$root/extensions/services/$service_id"
    mkdir -p "$dir"
    "$@" "$dir"
}

service_core_llm() {
    local dir="$1"
    cat > "$dir/manifest.yaml" <<'EOF'
schema_version: ods.services.v1

service:
  id: llama-server
  name: llama-server
  aliases: [llm]
  container_name: ods-llama-server
  port: 8080
  external_port_env: OLLAMA_PORT
  external_port_default: 8080
  health: /health
  type: docker
  gpu_backends: [amd, nvidia]
  category: core
  depends_on: []
EOF
}

service_search_valid() {
    local dir="$1"
    cat > "$dir/manifest.yaml" <<'EOF'
schema_version: ods.services.v1

service:
  id: search
  name: Search
  aliases: [search-ui]
  container_name: ods-search
  port: 8080
  external_port_env: SEARCH_PORT
  external_port_default: 8888
  health: /healthz
  type: docker
  gpu_backends: [amd, nvidia]
  compose_file: compose.yaml
  category: recommended
  depends_on: [llama-server]

features:
  - id: search-ui
    name: Search UI
    description: Search the web privately
    category: productivity
    priority: 3
    requirements:
      services: [search]
    enabled_services_all: [search]
EOF
    cat > "$dir/compose.yaml" <<'EOF'
services:
  search:
    image: example/search:latest
    container_name: ods-search
    ports:
      - "127.0.0.1:${SEARCH_PORT:-8888}:8080"
    healthcheck:
      test: ["CMD", "wget", "--spider", "--quiet", "http://localhost:8080/healthz"]
EOF
}

service_image_valid() {
    local dir="$1"
    cat > "$dir/manifest.yaml" <<'EOF'
schema_version: ods.services.v1

service:
  id: image-gen
  name: Image Generation
  aliases: []
  container_name: ods-image-gen
  port: 8188
  external_port_env: IMAGE_GEN_PORT
  external_port_default: 8188
  health: /
  type: docker
  gpu_backends: [amd, nvidia]
  compose_file: compose.yaml
  category: optional
  depends_on: []
EOF
    cat > "$dir/compose.yaml" <<'EOF'
services: {}
EOF
    cat > "$dir/compose.amd.yaml" <<'EOF'
services:
  image-gen:
    image: example/image-gen:amd
    container_name: ods-image-gen
    ports:
      - "${IMAGE_GEN_PORT:-8188}:8188"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8188/"]
EOF
    cat > "$dir/compose.nvidia.yaml" <<'EOF'
services:
  image-gen:
    image: example/image-gen:nvidia
    container_name: ods-image-gen
    ports:
      - "${IMAGE_GEN_PORT:-8188}:8188"
    healthcheck:
      test: ["CMD", "wget", "--spider", "--quiet", "http://localhost:8188/"]
EOF
}

service_host_valid() {
    local dir="$1"
    cat > "$dir/manifest.yaml" <<'EOF'
schema_version: ods.services.v1

service:
  id: opencode
  name: OpenCode
  aliases: [code]
  container_name: ""
  port: 3003
  external_port_default: 3003
  health: /
  type: host-systemd
  gpu_backends: [amd, nvidia]
  category: optional
  depends_on: []
EOF
}

create_valid_project() {
    local root="$1"
    write_service "$root" "llama-server" service_core_llm
    write_service "$root" "search" service_search_valid
    write_service "$root" "image-gen" service_image_valid
    write_service "$root" "opencode" service_host_valid
}

run_audit() {
    python3 "$AUDIT_SCRIPT" --project-dir "$1" "${@:2}"
}

assert_json_value() {
    local file="$1"
    local expr="$2"
    python3 - "$file" "$expr" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
expr = sys.argv[2]
value = eval(expr, {"payload": payload})
if isinstance(value, bool):
    raise SystemExit(0 if value else 1)
print(value)
PY
}

header "1" "Valid Project Passes Cleanly"
root=$(make_fixture_root)
trap 'rm -rf "$root" "${root2:-}" "${root3:-}" "${root4:-}" "${root5:-}" "${root6:-}" "${root7:-}"' EXIT
create_valid_project "$root"
report=$(mktemp)
if run_audit "$root" --json > "$report"; then
    pass "valid fixture audits successfully"
else
    fail "valid fixture should pass"
fi
if assert_json_value "$report" "payload['summary']['result'] == 'pass'" >/dev/null; then
    pass "valid fixture reports pass"
else
    fail "valid fixture JSON did not report pass"
fi
if assert_json_value "$report" "payload['summary']['warnings'] == 0" >/dev/null; then
    pass "valid fixture reports zero warnings"
else
    fail "valid fixture unexpectedly reported warnings"
fi

header "2" "Missing Dependency Is Rejected"
root2=$(make_fixture_root)
create_valid_project "$root2"
python3 - "$root2/extensions/services/search/manifest.yaml" <<'PY'
import yaml
import sys
path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
doc["service"]["depends_on"] = ["missing-service"]
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
report2=$(mktemp)
if run_audit "$root2" --json > "$report2" 2>/dev/null; then
    fail "missing dependency should fail"
else
    pass "missing dependency fails audit"
fi
if assert_json_value "$report2" "any(issue['code'] == 'dependency-missing' for svc in payload['services'] for issue in svc['issues'])" >/dev/null; then
    pass "missing dependency is reported with the right code"
else
    fail "missing dependency code was not reported"
fi

header "3" "Alias Collisions Are Rejected"
root3=$(make_fixture_root)
create_valid_project "$root3"
python3 - "$root3/extensions/services/opencode/manifest.yaml" <<'PY'
import yaml
import sys
path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
doc["service"]["aliases"] = ["search-ui"]
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
report3=$(mktemp)
if run_audit "$root3" --json > "$report3" 2>/dev/null; then
    fail "alias collision should fail"
else
    pass "alias collision fails audit"
fi
if assert_json_value "$report3" "any(issue['code'] == 'alias-collision' for svc in payload['services'] for issue in svc['issues'])" >/dev/null; then
    pass "alias collision is reported"
else
    fail "alias collision code was not reported"
fi

header "4" "GPU Stub Requires Matching Overlays"
root4=$(make_fixture_root)
create_valid_project "$root4"
rm -f "$root4/extensions/services/image-gen/compose.nvidia.yaml"
report4=$(mktemp)
if run_audit "$root4" --json > "$report4" 2>/dev/null; then
    fail "missing overlay should fail"
else
    pass "missing overlay fails audit"
fi
if assert_json_value "$report4" "any(issue['code'] == 'overlay-required' for svc in payload['services'] for issue in svc['issues'])" >/dev/null; then
    pass "missing overlay is reported"
else
    fail "missing overlay code was not reported"
fi

header "5" "Compose Port Mismatch Is Rejected"
root5=$(make_fixture_root)
create_valid_project "$root5"
python3 - "$root5/extensions/services/search/compose.yaml" <<'PY'
import yaml
import sys
path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
doc["services"]["search"]["ports"] = ["127.0.0.1:${SEARCH_PORT:-8888}:9090"]
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
report5=$(mktemp)
if run_audit "$root5" --json > "$report5" 2>/dev/null; then
    fail "port mismatch should fail"
else
    pass "port mismatch fails audit"
fi
if assert_json_value "$report5" "any(issue['code'] == 'compose-port-mismatch' for svc in payload['services'] for issue in svc['issues'])" >/dev/null; then
    pass "port mismatch is reported"
else
    fail "port mismatch code was not reported"
fi

header "6" "Strict Mode Fails On Warnings"
root6=$(make_fixture_root)
create_valid_project "$root6"
cp "$root6/extensions/services/image-gen/compose.nvidia.yaml" \
   "$root6/extensions/services/image-gen/compose.apple.yaml"
report6=$(mktemp)
if run_audit "$root6" --json > "$report6"; then
    pass "extra overlay only warns in normal mode"
else
    fail "normal mode should tolerate warning-only fixture"
fi
if assert_json_value "$report6" "payload['summary']['warnings'] > 0" >/dev/null; then
    pass "warning count is reported"
else
    fail "warning fixture did not report warnings"
fi
if run_audit "$root6" --strict >/dev/null 2>&1; then
    fail "strict mode should fail on warnings"
else
    pass "strict mode converts warnings into failure"
fi

header "7" "External Port Zero Is Accepted"
root7=$(make_fixture_root)
create_valid_project "$root7"
python3 - "$root7/extensions/services/search/manifest.yaml" <<'PY'
import yaml
import sys
path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
doc["service"].pop("external_port_env", None)
doc["service"]["external_port_default"] = 0
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
report7=$(mktemp)
if run_audit "$root7" --json > "$report7" 2>/dev/null; then
    pass "external_port_default=0 fixture audits successfully"
else
    fail "external_port_default=0 should be allowed for internal-only services"
fi

echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}${BOLD}${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
