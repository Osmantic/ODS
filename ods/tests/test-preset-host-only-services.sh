#!/bin/bash
# ============================================================================
# ODS CLI preset: host-managed services (OpenCode)
# ============================================================================
# OpenCode runs on the host: its extension directory has a manifest and no
# Compose file. `preset save` listed it as `disabled:opencode`, and
# `preset load` then stopped with "No Compose selection file for opencode",
# so a preset saved on a default install could never be loaded back.
# - `preset save` must not list host-managed services, and must still record
#   enabled and disabled Compose extensions.
# - `preset load` must ignore such entries in presets saved before the fix
#   and restore the rest of the preset.
#
# Usage: ./tests/test-preset-host-only-services.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASSED=0
FAILED=0
pass() { echo "  PASS $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  FAIL $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-preset-host-only.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/lib" "$FIXTURE/scripts" "$FIXTURE/bin" "$FIXTURE/data" \
    "$FIXTURE/extensions/services/opencode"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
cp "$ROOT_DIR/scripts/extension-selection.py" "$FIXTURE/scripts/"
cp "$ROOT_DIR/scripts/stop-owned-containers.py" "$FIXTURE/scripts/"
# The shipped OpenCode manifest: type host-systemd, no Compose file.
cp "$ROOT_DIR/extensions/services/opencode/manifest.yaml" "$FIXTURE/extensions/services/opencode/"
printf '#!/bin/sh\n[ "$1" = ps ]\n' > "$FIXTURE/bin/docker"
chmod +x "$FIXTURE/bin/docker"
export PATH="$FIXTURE/bin:$PATH"
printf 'services: {}\n' > "$FIXTURE/docker-compose.base.yml"
printf 'GPU_BACKEND=nvidia\nLLM_MODEL=model-a\n' > "$FIXTURE/.env"

# write_ext <id> <selection file: compose.yaml or compose.yaml.disabled>
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
    echo "services: {}" > "$dir/$2"
}
write_ext on compose.yaml
write_ext off compose.yaml.disabled
ON="$FIXTURE/extensions/services/on"
HOST="$FIXTURE/extensions/services/opencode"
LIST="$FIXTURE/presets/base/extensions.list"

run_cli() {
    # Callers assert on output and files; a failing CLI must not end the test.
    ODS_HOME="$FIXTURE" bash "$FIXTURE/ods-cli" "$@" 2>&1 || true
}

echo ""
echo "=== CLI preset with a host-managed service ==="

output=$(run_cli preset save base)
if [[ -f "$LIST" ]] && ! grep -q "opencode" "$LIST"; then
    pass "save leaves out the host-managed service"
else
    fail "save listed the host-managed service: $(cat "$LIST" 2>/dev/null) $output"
fi
if grep -qx 'enabled:on' "$LIST" 2>/dev/null && grep -qx 'disabled:off' "$LIST"; then
    pass "save still records enabled and disabled Compose extensions"
else
    fail "save lost a Compose extension: $(cat "$LIST" 2>/dev/null) $output"
fi

output=$(printf 'y\n' | run_cli preset load base)
if echo "$output" | grep -q "Preset 'base' loaded"; then
    pass "load succeeds on an install with a host-managed service"
else
    fail "load failed: $output"
fi

# A preset saved before this fix lists the host service. Load must skip that
# entry and still restore the Compose selection and the settings.
printf 'enabled:on\ndisabled:off\ndisabled:opencode\n' > "$LIST"
mv "$ON/compose.yaml" "$ON/compose.yaml.disabled"
printf 'GPU_BACKEND=nvidia\nLLM_MODEL=model-b\n' > "$FIXTURE/.env"
output=$(printf 'y\n' | run_cli preset load base)
if echo "$output" | grep -q "Preset 'base' loaded" && [[ -f "$ON/compose.yaml" ]] &&
   grep -qx 'LLM_MODEL=model-a' "$FIXTURE/.env"; then
    pass "load of an older preset skips the host entry and restores the rest"
else
    fail "load of an older preset failed: $output"
fi
if [[ ! -e "$HOST/compose.yaml" && ! -e "$HOST/compose.yaml.disabled" ]]; then
    pass "load leaves the host-managed service without a Compose file"
else
    fail "load created a Compose selection file for the host service"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
