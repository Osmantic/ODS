#!/bin/bash
# ============================================================================
# ODS CLI preset: host-only services and secret handling
# ============================================================================
# - `preset save` must not list host-managed services (no compose.yaml), and
#   `preset load` must ignore such entries in older presets. Otherwise the
#   restore helper fails with "No Compose selection file".
# - Presets must not carry secrets, and `preset load` must keep the current
#   install's secrets while applying the preset's other settings.
# - Preset names are confined to the presets directory.
#
# Usage: ./tests/test-preset-host-only-and-secrets.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASSED=0
FAILED=0
pass() { echo "  PASS $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  FAIL $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-preset-secrets.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/lib" "$FIXTURE/scripts" "$FIXTURE/bin" "$FIXTURE/extensions/services" "$FIXTURE/data"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
cp "$ROOT_DIR/scripts/extension-selection.py" "$FIXTURE/scripts/"
cp "$ROOT_DIR/scripts/stop-owned-containers.py" "$FIXTURE/scripts/"
printf '#!/bin/sh\n[ "$1" = ps ]\n' > "$FIXTURE/bin/docker"
chmod +x "$FIXTURE/bin/docker"
export PATH="$FIXTURE/bin:$PATH"
printf 'services: {}\n' > "$FIXTURE/docker-compose.base.yml"
cat > "$FIXTURE/.env.schema.json" <<'EOF'
{"properties": {
  "WEBUI_SECRET_KEY": {"type": "string", "secret": true},
  "N8N_ENCRYPTION_KEY": {"type": "string", "secret": true},
  "LLM_MODEL": {"type": "string"}
}}
EOF

# write_ext <id> <with_compose:0|1>
write_ext() {
    local dir="$FIXTURE/extensions/services/$1"
    mkdir -p "$dir"
    cat > "$dir/manifest.yaml" <<EOF
schema_version: ods.services.v1
service:
  id: $1
  name: $1
  container_name: ods-$1
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: []
EOF
    if [[ "$2" == 1 ]]; then echo "services: {}" > "$dir/compose.yaml"; fi
}
write_ext dsvc 1
write_ext hostonly 0

run_cli() {
    ODS_HOME="$FIXTURE" bash "$FIXTURE/ods-cli" "$@" 2>&1 || true
}

cat > "$FIXTURE/.env" <<'EOF'
GPU_BACKEND=nvidia
LLM_MODEL=model-a
WEBUI_SECRET_KEY=old-secret
N8N_ENCRYPTION_KEY=old-n8n
EOF
chmod 600 "$FIXTURE/.env"

echo ""
echo "=== CLI preset host-only services and secrets ==="

# (a) save + load on an install with a host-only service
output=$(run_cli preset save base)
if grep -q "hostonly" "$FIXTURE/presets/base/extensions.list" 2>/dev/null; then
    fail "save listed host-only service: $(cat "$FIXTURE/presets/base/extensions.list")"
else
    pass "save skips host-only service"
fi
output=$(printf 'y\n' | run_cli preset load base)
if echo "$output" | grep -q "Preset 'base' loaded"; then
    pass "load succeeds with a host-only service installed"
else
    fail "load failed: $output"
fi

# Legacy preset that lists the host-only service still loads.
printf 'enabled:dsvc\ndisabled:hostonly\n' > "$FIXTURE/presets/base/extensions.list"
output=$(printf 'y\n' | run_cli preset load base)
if echo "$output" | grep -q "Preset 'base' loaded"; then
    pass "load ignores host-only entry in older preset"
else
    fail "load rejected host-only entry: $output"
fi

# (c) saved preset contains no secret keys
if grep -qE '^(WEBUI_SECRET_KEY|N8N_ENCRYPTION_KEY)=' "$FIXTURE/presets/base/env"; then
    fail "saved preset env contains secrets"
elif grep -q '^LLM_MODEL=model-a$' "$FIXTURE/presets/base/env"; then
    pass "saved preset env keeps settings and excludes secrets"
else
    fail "saved preset env lost settings: $(cat "$FIXTURE/presets/base/env")"
fi

# (b) + (d) legacy preset with secrets, after a secret rotation
cat > "$FIXTURE/presets/base/env" <<'EOF'
GPU_BACKEND=nvidia
LLM_MODEL=model-b
WEBUI_SECRET_KEY=old-secret
N8N_ENCRYPTION_KEY=old-n8n
EOF
sed -i 's/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=rotated/; s/^N8N_ENCRYPTION_KEY=.*/N8N_ENCRYPTION_KEY=rotated-n8n/' "$FIXTURE/.env"
output=$(printf 'y\n' | run_cli preset load base)
if grep -q '^WEBUI_SECRET_KEY=rotated$' "$FIXTURE/.env" && grep -q '^N8N_ENCRYPTION_KEY=rotated-n8n$' "$FIXTURE/.env"; then
    pass "load keeps current secrets over legacy preset secrets"
else
    fail "load overwrote secrets: $(cat "$FIXTURE/.env")"
fi
if grep -q '^LLM_MODEL=model-b$' "$FIXTURE/.env"; then
    pass "load applies non-secret preset settings"
else
    fail "load did not apply settings: $output"
fi
if [[ -f "$FIXTURE/.env.preset-backup" ]] && [[ "$(stat -c %a "$FIXTURE/.env")" == 600 ]]; then
    pass "load keeps a backup and mode 0600"
else
    fail "missing backup or wrong mode"
fi

# Invalid env content is rejected without touching .env
before=$(cat "$FIXTURE/.env")
printf 'LLM_MODEL=model-c\nnot a valid line\n' > "$FIXTURE/presets/base/env"
output=$(printf 'y\n' | run_cli preset load base)
if [[ "$(cat "$FIXTURE/.env")" == "$before" ]] && echo "$output" | grep -q "Invalid preset"; then
    pass "load rejects malformed preset env"
else
    fail "malformed env accepted: $output"
fi

# (e) bad preset names
for bad in "../evil" ".hidden" "a/b"; do
    output=$(printf 'y\n' | run_cli preset save "$bad")
    if echo "$output" | grep -q "Invalid preset name" && [[ ! -e "$FIXTURE/evil" ]]; then
        pass "save rejects name '$bad'"
    else
        fail "save accepted name '$bad': $output"
    fi
done
mkdir -p "$FIXTURE/victim"
output=$(printf 'y\n' | run_cli preset delete "../victim")
if [[ -d "$FIXTURE/victim" ]] && echo "$output" | grep -q "Invalid preset name"; then
    pass "delete rejects traversal name"
else
    fail "delete accepted traversal name: $output"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
