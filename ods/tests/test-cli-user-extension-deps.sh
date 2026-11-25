#!/bin/bash
# ============================================================================
# ODS CLI user-extension dependency resolution test
# ============================================================================
# enable/disable/purge must treat dashboard-installed user extensions
# (data/user-extensions/) as first-class when resolving inter-extension
# dependencies. The service registry loads both trees, but the compose.yaml
# existence checks used to look only under extensions/services/, so:
#   - enable flagged an *enabled* user-extension dependency as missing
#   - disable skipped enabled user-extension dependents (no warning)
#   - purge deleted data of a user extension that was still enabled
#
# Strategy: build a throwaway INSTALL_DIR fixture (ODS_HOME) with one
# built-in and one user extension, then run the real CLI against it.
# No docker required — every docker call on these paths is soft-guarded.
#
# Usage: ./tests/test-cli-user-extension-deps.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-user-ext-deps.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

# ---------------------------------------------------------------------------
# Fixture: minimal install dir the CLI accepts (check_install + sr_load)
# ---------------------------------------------------------------------------
mkdir -p "$FIXTURE/bin" "$FIXTURE/lib" "$FIXTURE/scripts" "$FIXTURE/extensions/services" "$FIXTURE/data/user-extensions"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
cp "$ROOT_DIR/scripts/extension-selection.py" "$FIXTURE/scripts/"
cat > "$FIXTURE/bin/docker" <<'SH'
#!/bin/sh
exit 0
SH
chmod +x "$FIXTURE/bin/docker"
: > "$FIXTURE/docker-compose.base.yml"
echo "GPU_BACKEND=nvidia" > "$FIXTURE/.env"

# write_ext <dir> <id> <depends_on-yaml-list>
write_ext() {
    local dir="$1" id="$2" deps="$3"
    mkdir -p "$dir"
    cat > "$dir/manifest.yaml" <<EOF
schema_version: ods.services.v1
service:
  id: $id
  name: $id
  container_name: ods-$id
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: $deps
EOF
    echo "services: {}" > "$dir/compose.yaml"
}

BUILTIN="$FIXTURE/extensions/services/bsvc"
USEREXT="$FIXTURE/data/user-extensions/usvc"

run_cli() {
    # Never let a non-zero CLI exit kill the test; callers assert on output/state
    ODS_HOME="$FIXTURE" PATH="$FIXTURE/bin:$PATH" bash "$FIXTURE/ods-cli" "$@" 2>&1 || true
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   CLI user-extension dependency test          ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. enable: an *enabled* user-extension dependency is not reported missing
# ---------------------------------------------------------------------------
write_ext "$BUILTIN" bsvc "[usvc]"
mv "$BUILTIN/compose.yaml" "$BUILTIN/compose.yaml.disabled"
write_ext "$USEREXT" usvc "[]"

output=$(printf '\n' | run_cli enable bsvc)
if echo "$output" | grep -q "depends on disabled services"; then
    fail "enable flags enabled user-extension dependency as missing: $output"
else
    pass "enable does not flag enabled user-extension dependency"
fi
if [[ -f "$BUILTIN/compose.yaml" ]]; then
    pass "enable activated the service"
else
    fail "enable did not activate the service: $output"
fi

# ---------------------------------------------------------------------------
# 2. enable: a *disabled* user-extension dependency is still reported missing
# ---------------------------------------------------------------------------
mv "$BUILTIN/compose.yaml" "$BUILTIN/compose.yaml.disabled"
mv "$USEREXT/compose.yaml" "$USEREXT/compose.yaml.disabled"

output=$(printf 'n\n' | run_cli enable bsvc)
if echo "$output" | grep -q "depends on disabled services: usvc"; then
    pass "enable still flags disabled user-extension dependency"
else
    fail "enable missed disabled user-extension dependency: $output"
fi
if [[ -f "$BUILTIN/compose.yaml.disabled" ]] && \
   echo "$output" | grep -q "Cancelled enabling bsvc"; then
    pass "declining dependency enable leaves the target disabled"
else
    fail "declining dependency enable changed selection or misreported result: $output"
fi

# ---------------------------------------------------------------------------
# 3. disable: an enabled user-extension dependent is refused
# ---------------------------------------------------------------------------
rm -rf "$BUILTIN" "$USEREXT"
write_ext "$BUILTIN" bsvc "[]"
write_ext "$USEREXT" usvc "[bsvc]"

output=$(printf 'n\n' | run_cli disable bsvc)
if echo "$output" | grep -q "depend on bsvc: usvc"; then
    pass "disable warns about enabled user-extension dependent"
else
    fail "disable missed enabled user-extension dependent: $output"
fi
if [[ -f "$BUILTIN/compose.yaml" ]]; then
    pass "disable preserved the selected dependency"
else
    fail "disable proceeded despite user answering no"
fi

# A selected Compose-only dependency is just as binding, and answering yes
# cannot override the safety check.
write_ext "$USEREXT" usvc "[]"
cat > "$USEREXT/compose.yaml" <<'YAML'
services:
  usvc:
    image: example:latest
    depends_on: [bsvc]
YAML
output=$(printf 'y\n' | run_cli disable bsvc)
if echo "$output" | grep -q "depend on bsvc: usvc" && [[ -f "$BUILTIN/compose.yaml" ]]; then
    pass "disable rejects a Compose-only dependent even after explicit yes"
else
    fail "disable allowed a selected Compose-only dependent: $output"
fi

# ---------------------------------------------------------------------------
# 4. disable: a *disabled* user-extension dependent does not warn
# ---------------------------------------------------------------------------
mv "$USEREXT/compose.yaml" "$USEREXT/compose.yaml.disabled"

output=$(run_cli disable bsvc </dev/null)
if echo "$output" | grep -q "depend on bsvc"; then
    fail "disable warned about a disabled dependent: $output"
else
    pass "disable skips disabled user-extension dependent"
fi
if [[ -f "$BUILTIN/compose.yaml.disabled" ]]; then
    pass "disable deactivated the service"
else
    fail "disable did not deactivate the service: $output"
fi

# ---------------------------------------------------------------------------
# 5. purge: an enabled user extension is rejected before deleting data
# ---------------------------------------------------------------------------
rm -rf "$BUILTIN" "$USEREXT"
write_ext "$USEREXT" usvc "[]"
mkdir -p "$FIXTURE/data/usvc"
echo "keep" > "$FIXTURE/data/usvc/marker"

output=$(printf 'usvc\n' | run_cli purge usvc)
if echo "$output" | grep -q "still enabled"; then
    pass "purge rejects enabled user extension"
else
    fail "purge did not reject enabled user extension: $output"
fi
if [[ -f "$FIXTURE/data/usvc/marker" ]]; then
    pass "purge preserved data of enabled user extension"
else
    fail "purge deleted data of enabled user extension"
fi

# ---------------------------------------------------------------------------
# 6. purge: a *disabled* user extension still purges after confirmation
# ---------------------------------------------------------------------------
mv "$USEREXT/compose.yaml" "$USEREXT/compose.yaml.disabled"

output=$(printf 'usvc\n' | run_cli purge usvc)
if [[ ! -d "$FIXTURE/data/usvc" ]]; then
    pass "purge removes data of disabled user extension"
else
    fail "purge left data of disabled user extension: $output"
fi

# ---------------------------------------------------------------------------
# 7. enable: a profiled-out core dependency does not force managed inference
# ---------------------------------------------------------------------------
rm -rf "$BUILTIN" "$USEREXT"
write_ext "$BUILTIN" bsvc "[]"
sed -i 's/category: optional/category: core/' "$BUILTIN/manifest.yaml"
mv "$BUILTIN/compose.yaml" "$BUILTIN/compose.yaml.disabled"
write_ext "$USEREXT" usvc "[bsvc]"
mv "$USEREXT/compose.yaml" "$USEREXT/compose.yaml.disabled"
output=$(run_cli enable usvc)
if [[ -f "$USEREXT/compose.yaml" && -f "$BUILTIN/compose.yaml.disabled" ]]; then
    pass "enable keeps a profiled-out core dependency disabled"
else
    fail "enable forced a profiled-out core dependency: $output"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
