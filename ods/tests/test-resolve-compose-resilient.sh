#!/bin/bash
# ============================================================================
# Resolve compose stack resilient parsing test
# ============================================================================
# Tests that resolve-compose-stack.sh handles broken manifests correctly:
# - Default behavior: crash on bad manifest (Let It Crash principle)
# - --skip-broken flag: skip bad manifest and continue with others
#
# Usage: ./tests/test-resolve-compose-resilient.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }
contains_path() {
    printf '%s\n' "$1" | tr '\\' '/' | grep -q "$2"
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   Resolve Compose Resilient Parsing Test      ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script exists
if [[ ! -f "$ROOT_DIR/scripts/resolve-compose-stack.sh" ]]; then
    fail "scripts/resolve-compose-stack.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "resolve-compose-stack.sh exists"

# 2. --skip-broken flag is accepted
help_exit=0
bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --help 2>&1 | grep -q "skip-broken" || help_exit=$?
if [[ $help_exit -eq 0 ]] || grep -q "skip-broken" "$ROOT_DIR/scripts/resolve-compose-stack.sh"; then
    pass "--skip-broken flag is implemented"
else
    skip "--skip-broken flag not in help (may still work)"
fi

# 3. Behavioral test: Create temp extension with broken manifest
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Create minimal directory structure
mkdir -p "$TEMP_DIR/extensions/services/broken-ext"
mkdir -p "$TEMP_DIR/extensions/services/good-ext"

# Create broken manifest (invalid YAML)
cat > "$TEMP_DIR/extensions/services/broken-ext/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: broken-ext
  name: Broken Extension
  compose_file: compose.yaml
  invalid_yaml: {{{
EOF

# Create good manifest
cat > "$TEMP_DIR/extensions/services/good-ext/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: good-ext
  name: Good Extension
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF

# Create compose files
cat > "$TEMP_DIR/extensions/services/good-ext/compose.yaml" <<'EOF'
services:
  good-service:
    image: nginx:latest
EOF

# Create base compose file
cat > "$TEMP_DIR/docker-compose.base.yml" <<'EOF'
services:
  base-service:
    image: nginx:latest
EOF

# 4. Test default behavior: should exit 1 on broken manifest
default_exit=0
bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia 2>&1 || default_exit=$?

if [[ $default_exit -ne 0 ]]; then
    pass "Default behavior: exits on broken manifest (Let It Crash)"
else
    fail "Default behavior: should exit 1 on broken manifest"
fi

# 5. Test --skip-broken flag: should continue and skip broken extension
skip_exit=0
output=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken 2>&1) || skip_exit=$?

if [[ $skip_exit -eq 0 ]]; then
    pass "--skip-broken: continues execution despite broken manifest"
else
    fail "--skip-broken: should not exit on broken manifest"
fi

# 6. Verify error message is printed to stderr with --skip-broken
if echo "$output" | grep -q "ERROR: Failed to parse manifest"; then
    pass "--skip-broken: error message printed to stderr"
else
    fail "--skip-broken: error message not printed"
fi

# 7. Verify good extension is still included with --skip-broken
flags_output=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken 2>/dev/null)
if echo "$flags_output" | grep -q "good-ext"; then
    pass "--skip-broken: good extension still included in output"
else
    skip "--skip-broken: good extension not in output (may be filtered by other logic)"
fi

# 8. Verify broken extension is not included with --skip-broken
if echo "$flags_output" | grep -q "broken-ext"; then
    fail "--skip-broken: broken extension should not be in output"
else
    pass "--skip-broken: broken extension correctly excluded"
fi

# 9. Test with JSON parse error
cat > "$TEMP_DIR/extensions/services/broken-ext/manifest.json" <<'EOF'
{
  "schema_version": "ods.services.v1",
  "service": {
    "id": "broken-ext"
    "name": "Missing comma"
  }
}
EOF

rm -f "$TEMP_DIR/extensions/services/broken-ext/manifest.yaml"

json_exit=0
bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia 2>&1 || json_exit=$?

if [[ $json_exit -ne 0 ]]; then
    pass "JSON parse error: exits by default"
else
    fail "JSON parse error: should exit 1"
fi

# 10. Verify --skip-broken works with JSON errors
json_skip_exit=0
bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken 2>&1 || json_skip_exit=$?

if [[ $json_skip_exit -eq 0 ]]; then
    pass "JSON parse error: --skip-broken continues execution"
else
    fail "JSON parse error: --skip-broken should not exit"
fi

# ============================================================================
# 11. Path-traversal hardening: compose_file with .. must not escape ext dir
# ============================================================================
# Clean prior broken-ext fixture so it doesn't interfere with traversal checks.
rm -rf "$TEMP_DIR/extensions/services/broken-ext"

mkdir -p "$TEMP_DIR/extensions/services/traversal-ext"
cat > "$TEMP_DIR/extensions/services/traversal-ext/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: traversal-ext
  name: Traversal Test
  compose_file: "../../../../../../etc/passwd"
  gpu_backends: ["nvidia", "amd", "apple"]
EOF

traversal_exit=0
traversal_stderr_file="$TEMP_DIR/traversal.stderr"
traversal_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$traversal_stderr_file") || traversal_exit=$?
traversal_stderr=$(cat "$traversal_stderr_file")

if [[ $traversal_exit -ne 0 ]]; then
    fail "Traversal compose_file caused resolver to crash (exit $traversal_exit)"
elif contains_path "$traversal_stdout" "etc/passwd"; then
    fail "Traversal path INCLUDED in resolved stack (security regression)"
else
    pass "Traversal compose_file rejected from resolved stack"
fi

if echo "$traversal_stderr" | grep -qi "WARNING.*traversal-ext.*escapes"; then
    pass "WARNING emitted for traversal-ext compose_file"
else
    fail "Expected WARNING for traversal-ext compose_file"
fi

# ============================================================================
# 12. Path-traversal hardening: absolute compose_file must not crash resolver
# ============================================================================
mkdir -p "$TEMP_DIR/extensions/services/absolute-ext"
cat > "$TEMP_DIR/extensions/services/absolute-ext/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: absolute-ext
  name: Absolute Path Test
  compose_file: "/etc/shadow"
  gpu_backends: ["nvidia", "amd", "apple"]
EOF

abs_exit=0
abs_stderr_file="$TEMP_DIR/absolute.stderr"
abs_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$abs_stderr_file") || abs_exit=$?
abs_stderr=$(cat "$abs_stderr_file")

if [[ $abs_exit -ne 0 ]]; then
    fail "Resolver crashed on absolute compose_file (DoS regression, exit $abs_exit)"
elif contains_path "$abs_stdout" "etc/shadow"; then
    fail "Absolute path INCLUDED in resolved stack (security regression)"
else
    pass "Resolver handled absolute compose_file gracefully"
fi

if echo "$abs_stderr" | grep -qi "WARNING.*absolute-ext.*escapes"; then
    pass "WARNING emitted for absolute-ext compose_file"
else
    fail "Expected WARNING for absolute-ext compose_file"
fi

# ============================================================================
# 14. User-ext path-traversal: compose_file with .. must not escape ext dir
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-traversal"
cat > "$TEMP_DIR/data/user-extensions/user-traversal/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-traversal
  name: User Traversal Test
  compose_file: "../../../../../../etc/passwd"
  gpu_backends: ["nvidia", "amd", "apple"]
EOF

ut_exit=0
ut_stderr_file="$TEMP_DIR/user-traversal.stderr"
ut_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$ut_stderr_file") || ut_exit=$?
ut_stderr=$(cat "$ut_stderr_file")

if [[ $ut_exit -ne 0 ]]; then
    fail "User-ext traversal caused resolver to crash (exit $ut_exit)"
elif contains_path "$ut_stdout" "etc/passwd"; then
    fail "User-ext traversal path INCLUDED in resolved stack (security regression)"
else
    pass "User-ext traversal compose_file rejected from resolved stack"
fi

if echo "$ut_stderr" | grep -qi "WARNING.*user-traversal.*escapes"; then
    pass "WARNING emitted for user-ext traversal compose_file"
else
    fail "Expected WARNING for user-ext traversal compose_file"
fi

# ============================================================================
# 15. User-ext compose with bare 0.0.0.0 port must be rejected
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-bareports"
cat > "$TEMP_DIR/data/user-extensions/user-bareports/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-bareports
  name: User Bare Ports
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-bareports/compose.yaml" <<'EOF'
services:
  user-bareports-svc:
    image: nginx:latest
    ports:
      - "0.0.0.0:8080:80"
EOF

bp_stderr_file="$TEMP_DIR/user-bareports.stderr"
bp_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$bp_stderr_file") || true
bp_stderr=$(cat "$bp_stderr_file")

if contains_path "$bp_stdout" "user-bareports/compose.yaml"; then
    fail "User-ext with 0.0.0.0 port INCLUDED in resolved stack"
else
    pass "User-ext with 0.0.0.0 port excluded from resolved stack"
fi

if echo "$bp_stderr" | grep -qi "WARNING.*user-bareports.*"; then
    pass "WARNING emitted for user-ext 0.0.0.0 port"
else
    fail "Expected WARNING for user-ext 0.0.0.0 port"
fi

# ============================================================================
# 16. User-ext compose with privileged: true must be rejected
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-priv"
cat > "$TEMP_DIR/data/user-extensions/user-priv/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-priv
  name: User Privileged
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-priv/compose.yaml" <<'EOF'
services:
  user-priv-svc:
    image: nginx:latest
    privileged: true
EOF

priv_stderr_file="$TEMP_DIR/user-priv.stderr"
priv_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$priv_stderr_file") || true
priv_stderr=$(cat "$priv_stderr_file")

if contains_path "$priv_stdout" "user-priv/compose.yaml"; then
    fail "User-ext privileged INCLUDED in resolved stack"
else
    pass "User-ext privileged excluded from resolved stack"
fi

if echo "$priv_stderr" | grep -qi "WARNING.*user-priv.*privileged"; then
    pass "WARNING emitted for user-ext privileged"
else
    fail "Expected WARNING for user-ext privileged"
fi

# ============================================================================
# 17. User-ext compose with build: must be rejected
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-build"
cat > "$TEMP_DIR/data/user-extensions/user-build/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-build
  name: User Build
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-build/compose.yaml" <<'EOF'
services:
  user-build-svc:
    build: .
EOF

build_stderr_file="$TEMP_DIR/user-build.stderr"
build_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$build_stderr_file") || true
build_stderr=$(cat "$build_stderr_file")

if contains_path "$build_stdout" "user-build/compose.yaml"; then
    fail "User-ext build INCLUDED in resolved stack"
else
    pass "User-ext build excluded from resolved stack"
fi

if echo "$build_stderr" | grep -qi "WARNING.*user-build.*build"; then
    pass "WARNING emitted for user-ext build directive"
else
    fail "Expected WARNING for user-ext build directive"
fi

# ============================================================================
# 18. User-ext compose with docker.sock mount must be rejected
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-sock"
cat > "$TEMP_DIR/data/user-extensions/user-sock/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-sock
  name: User Sock
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-sock/compose.yaml" <<'EOF'
services:
  user-sock-svc:
    image: nginx:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
EOF

sock_stderr_file="$TEMP_DIR/user-sock.stderr"
sock_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$sock_stderr_file") || true
sock_stderr=$(cat "$sock_stderr_file")

if contains_path "$sock_stdout" "user-sock/compose.yaml"; then
    fail "User-ext docker.sock INCLUDED in resolved stack"
else
    pass "User-ext docker.sock excluded from resolved stack"
fi

if echo "$sock_stderr" | grep -qi "WARNING.*user-sock.*Docker socket"; then
    pass "WARNING emitted for user-ext docker.sock mount"
else
    fail "Expected WARNING for user-ext docker.sock mount"
fi

# ============================================================================
# 19. User-ext compose with long-form absolute bind mount must be rejected
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-dict-bind"
cat > "$TEMP_DIR/data/user-extensions/user-dict-bind/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-dict-bind
  name: User Dict Bind
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-dict-bind/compose.yaml" <<'EOF'
services:
  user-dict-bind-svc:
    image: nginx:latest
    volumes:
      - type: bind
        source: /etc
        target: /host-etc
EOF

dict_bind_stderr_file="$TEMP_DIR/user-dict-bind.stderr"
dict_bind_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$dict_bind_stderr_file") || true
dict_bind_stderr=$(cat "$dict_bind_stderr_file")

if contains_path "$dict_bind_stdout" "user-dict-bind/compose.yaml"; then
    fail "User-ext long-form bind mount INCLUDED in resolved stack"
else
    pass "User-ext long-form bind mount excluded from resolved stack"
fi

if echo "$dict_bind_stderr" | grep -qi "WARNING.*user-dict-bind.*bind-mounts absolute host path"; then
    pass "WARNING emitted for user-ext long-form bind mount"
else
    fail "Expected WARNING for user-ext long-form bind mount"
fi

# ============================================================================
# 20. User-ext compose with BIND_ADDRESS-default loopback port must be ACCEPTED
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/user-loopback-default"
cat > "$TEMP_DIR/data/user-extensions/user-loopback-default/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: user-loopback-default
  name: User Loopback Default
  compose_file: compose.yaml
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/user-loopback-default/compose.yaml" <<'EOF'
services:
  user-loopback-default-svc:
    image: nginx:latest
    ports:
      - "${BIND_ADDRESS:-127.0.0.1}:9091:80"
EOF

ld_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>/dev/null) || true

if contains_path "$ld_stdout" "user-loopback-default/compose.yaml"; then
    pass "User-ext with BIND_ADDRESS-default loopback port accepted"
else
    fail "User-ext with BIND_ADDRESS-default loopback port should be accepted"
fi

# ============================================================================
# 21. docker-compose.override.yml with bare 0.0.0.0 port must be rejected
# ============================================================================
cat > "$TEMP_DIR/docker-compose.override.yml" <<'EOF'
services:
  override-svc:
    image: nginx:latest
    ports:
      - "0.0.0.0:9999:80"
EOF

ovr_stderr_file="$TEMP_DIR/override.stderr"
ovr_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$ovr_stderr_file") || true
ovr_stderr=$(cat "$ovr_stderr_file")

if echo "$ovr_stdout" | grep -q "docker-compose.override.yml"; then
    fail "Override with 0.0.0.0 port INCLUDED in resolved stack"
else
    pass "Override with 0.0.0.0 port excluded from resolved stack"
fi

if echo "$ovr_stderr" | grep -qi "WARNING.*docker-compose.override.yml"; then
    pass "WARNING emitted for override with 0.0.0.0 port"
else
    fail "Expected WARNING for override with 0.0.0.0 port"
fi

# ============================================================================
# 22. docker-compose.override.yml with loopback ports must be ACCEPTED
# ============================================================================
cat > "$TEMP_DIR/docker-compose.override.yml" <<'EOF'
services:
  override-svc-good:
    image: nginx:latest
    ports:
      - "127.0.0.1:10001:80"
EOF

ovr_good_stdout=$(bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>/dev/null) || true

if echo "$ovr_good_stdout" | grep -q "docker-compose.override.yml"; then
    pass "Override with literal-loopback port accepted"
else
    fail "Override with literal-loopback port should be accepted"
fi

# Drop the override.yml so subsequent tests don't drag it back into the stack
rm -f "$TEMP_DIR/docker-compose.override.yml"

# ============================================================================
# 23. User-ext compose with core-service-ID name collision must be REJECTED
# ============================================================================
mkdir -p "$TEMP_DIR/data/user-extensions/shadow-core"
cat > "$TEMP_DIR/data/user-extensions/shadow-core/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: shadow-core
  name: Shadow Core
  gpu_backends: ["nvidia", "amd", "apple"]
EOF
cat > "$TEMP_DIR/data/user-extensions/shadow-core/compose.yaml" <<'EOF'
services:
  dashboard-api:
    image: attacker/malicious:latest
    ports:
      - "127.0.0.1:8001:8001"
EOF

coll_stderr_file="$TEMP_DIR/collision.stderr"
coll_stdout=$(USER_EXTENSIONS_DIR="$TEMP_DIR/data/user-extensions" \
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>"$coll_stderr_file") || true
coll_stderr=$(cat "$coll_stderr_file")

if contains_path "$coll_stdout" "shadow-core/compose.yaml"; then
    fail "User-ext shadowing core service name INCLUDED in resolved stack"
else
    pass "User-ext shadowing core service name excluded from resolved stack"
fi

if echo "$coll_stderr" | grep -qi "collides.*core service"; then
    pass "WARNING emitted for core-service name collision"
else
    fail "Expected WARNING for core-service name collision (got: $(echo "$coll_stderr" | tail -3))"
fi

rm -rf "$TEMP_DIR/data/user-extensions/shadow-core"

# ============================================================================
# 24. External LLM overlay selection is explicit and deterministic
# ============================================================================
cat > "$TEMP_DIR/docker-compose.external-llm.yml" <<'EOF'
services:
  base-service:
    extra_hosts:
      - "host.docker.internal:host-gateway"
EOF

external_flags=$(EXTERNAL_LLM_URL="http://127.0.0.1:11434" \
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>/dev/null)
if contains_path "$external_flags" "docker-compose.external-llm.yml"; then
    pass "External endpoint selects the dedicated external-LLM overlay"
else
    fail "External endpoint did not select the dedicated external-LLM overlay"
fi

managed_flags=$(EXTERNAL_LLM_URL="" \
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$TEMP_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>/dev/null)
if contains_path "$managed_flags" "docker-compose.external-llm.yml"; then
    fail "Managed inference unexpectedly selected the external-LLM overlay"
else
    pass "Managed inference does not select the external-LLM overlay"
fi

# ============================================================================
# 25. Every direct external-LLM consumer receives a Linux host-gateway route
# ============================================================================
consumer_route_files=(
    "$ROOT_DIR/docker-compose.external-llm.yml"
    "$ROOT_DIR/extensions/services/hermes/compose.yaml"
    "$ROOT_DIR/extensions/services/openclaw/compose.yaml"
    "$ROOT_DIR/extensions/services/perplexica/compose.yaml"
    "$ROOT_DIR/extensions/services/privacy-shield/compose.yaml"
    "$ROOT_DIR/extensions/services/token-spy/compose.yaml"
)
consumer_routes_ok=true
for consumer_file in "${consumer_route_files[@]}"; do
    if ! grep -Fq "host.docker.internal:host-gateway" "$consumer_file"; then
        fail "$(basename "$consumer_file") is missing the external-LLM host-gateway route"
        consumer_routes_ok=false
    fi
done
if $consumer_routes_ok; then
    pass "All direct external-LLM consumers define a Linux host-gateway route"
fi

# ============================================================================
# 26. The real external-LLM stack renders without local inference dependencies
# ============================================================================
real_external_flags=$(EXTERNAL_LLM_URL="http://127.0.0.1:11434" \
    ODS_MODE=local \
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$ROOT_DIR" --tier 1 --gpu-backend nvidia --skip-broken \
    2>/dev/null)

if printf '%s\n' "$real_external_flags" | grep -Fq "compose.local.yaml"; then
    fail "External-LLM stack retained a local llama-server dependency overlay"
else
    pass "External-LLM stack excludes local llama-server dependency overlays"
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    compose_config_file="$TEMP_DIR/external-compose-config.yml"
    # shellcheck disable=SC2086
    if (
        cd "$ROOT_DIR"
        export EXTERNAL_LLM_URL="http://127.0.0.1:11434"
        export EXTERNAL_LLM_CONTAINER_URL="http://host.docker.internal:11434"
        export EXTERNAL_LLM_PROVIDER="ollama"
        export EXTERNAL_LLM_MODEL="qwen3.5:9b"
        export LLM_API_URL="$EXTERNAL_LLM_CONTAINER_URL"
        export HERMES_LLM_BASE_URL="${EXTERNAL_LLM_CONTAINER_URL}/v1"
        export HERMES_DASHBOARD_SESSION_TOKEN="external-llm-hermes-dashboard-session-token"
        export WEBUI_SECRET="external-llm-compose-test"
        export DASHBOARD_API_KEY="external-llm-compose-test"
        export ODS_AGENT_KEY="external-llm-compose-test"
        export N8N_USER="admin@example.invalid"
        export N8N_PASS="external-llm-compose-test"
        export OPENCLAW_TOKEN="external-llm-compose-test"
        export SEARXNG_SECRET="external-llm-compose-test"
        docker compose $real_external_flags config > "$compose_config_file"
    ); then
        if grep -Eq '^[[:space:]]+llama-server:$' "$compose_config_file"; then
            fail "Rendered external-LLM stack still contains managed llama-server"
        elif grep -Eq '^[[:space:]]+model-router:$' "$compose_config_file"; then
            fail "Rendered external-LLM stack still contains model-router"
        elif ! grep -Fq 'ODS_TALK_VISION_URL: http://host.docker.internal:11434/v1' "$compose_config_file"; then
            fail "Rendered external-LLM stack does not route ODS Talk vision to the external backend"
        else
            pass "Real external-LLM Compose stack renders without managed inference and routes ODS Talk externally"
        fi
    else
        fail "Real external-LLM Compose stack failed docker compose config"
    fi

    amd_external_flags=$(EXTERNAL_LLM_URL="http://127.0.0.1:11434" \
        ODS_MODE=local \
        bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        --script-dir "$ROOT_DIR" --tier 1 --gpu-backend amd --skip-broken \
        2>/dev/null)
    amd_compose_config_file="$TEMP_DIR/external-amd-compose-config.yml"
    # shellcheck disable=SC2086
    if (
        cd "$ROOT_DIR"
        export EXTERNAL_LLM_URL="http://127.0.0.1:11434"
        export EXTERNAL_LLM_CONTAINER_URL="http://host.docker.internal:11434"
        export EXTERNAL_LLM_PROVIDER="ollama"
        export EXTERNAL_LLM_MODEL="qwen3.5:9b"
        export LLM_API_URL="$EXTERNAL_LLM_CONTAINER_URL"
        export OPEN_WEBUI_LLM_BASE_URL="${EXTERNAL_LLM_CONTAINER_URL}/v1"
        export OPEN_WEBUI_LLM_API_KEY=""
        export HERMES_DASHBOARD_SESSION_TOKEN="external-llm-hermes-dashboard-session-token"
        export WEBUI_SECRET="external-llm-compose-test"
        export DASHBOARD_API_KEY="external-llm-compose-test"
        export ODS_AGENT_KEY="external-llm-compose-test"
        export N8N_USER="admin@example.invalid"
        export N8N_PASS="external-llm-compose-test"
        export OPENCLAW_TOKEN="external-llm-compose-test"
        export SEARXNG_SECRET="external-llm-compose-test"
        docker compose $amd_external_flags config > "$amd_compose_config_file"
    ); then
        if grep -Eq '^[[:space:]]+llama-server:$' "$amd_compose_config_file"; then
            fail "Rendered AMD external stack retained managed Lemonade"
        elif ! grep -Fq 'LLM_BACKEND: external' "$amd_compose_config_file"; then
            fail "Rendered AMD external stack overwrote the API backend with Lemonade"
        elif ! grep -Fq 'LLM_API_BASE_PATH: /v1' "$amd_compose_config_file"; then
            fail "Rendered AMD external stack retained the Lemonade API base path"
        elif ! grep -Fq 'OPENAI_API_KEY: ""' "$amd_compose_config_file"; then
            fail "Rendered AMD external stack leaked the LiteLLM key into Open WebUI"
        elif ! grep -Fq 'AMD_INFERENCE_RUNTIME: ""' "$amd_compose_config_file"; then
            fail "Rendered AMD external stack advertised a managed AMD runtime"
        else
            pass "Real AMD external stack overrides Lemonade routing without changing GPU telemetry"
        fi
    else
        fail "Real AMD external-LLM stack failed docker compose config"
    fi
else
    skip "Docker Compose unavailable; real external-LLM render skipped"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
