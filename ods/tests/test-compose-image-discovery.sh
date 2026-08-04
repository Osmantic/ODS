#!/usr/bin/env bash
# ============================================================================
# ODS Compose image discovery tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

assert_contains_line() {
    local file="$1" needle="$2" label="$3"
    if grep -Fxq -- "$needle" "$file"; then
        pass "$label"
    else
        fail "$label"
        echo "    missing line: $needle"
    fi
}

assert_not_contains_line() {
    local file="$1" needle="$2" label="$3"
    if grep -Fxq -- "$needle" "$file"; then
        fail "$label"
        echo "    unexpected line: $needle"
    else
        pass "$label"
    fi
}

cat > "$TMP_DIR/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "compose" && "$*" == *"config --format json"* ]]; then
  cat <<'JSON'
{
  "services": {
    "llama-server": {
      "image": "ghcr.io/ggml-org/llama.cpp:server-b8248"
    },
    "dashboard": {
      "build": {"context": "./extensions/services/dashboard"},
      "image": "ods-dashboard:latest"
    },
    "dashboard-api": {
      "build": {"context": "./extensions/services/dashboard-api"}
    },
    "hermes-proxy": {
      "image": "caddy:2.11.3-alpine"
    },
    "local-helper": {
      "image": "docker.io/library/ods-helper:latest"
    },
    "perplexica": {
      "image": "itzcrazykns1337/perplexica:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e"
    },
    "duplicate": {
      "image": "caddy:2.11.3-alpine"
    }
  }
}
JSON
  exit 0
fi
exit 1
EOF
chmod +x "$TMP_DIR/docker"

source "$ROOT_DIR/installers/lib/compose-images.sh"

# ── ODS_PYTHON_CMD resolution ────────────────────────────────────────────────
#
# ODS_PYTHON_CMD is a command, not necessarily a path. install-macos.sh and
# scripts/pre-download.sh export the plain name "python3" as readily as a venv
# path, so both forms must resolve to the interpreter the installer chose.

mkdir -p "$TMP_DIR/pybin"
cat > "$TMP_DIR/pybin/chosen-python" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP_DIR/pybin/chosen-python"

resolved="$(ODS_PYTHON_CMD="$TMP_DIR/pybin/chosen-python" _ods_compose_python_cmd)"
if [[ "$resolved" == "$TMP_DIR/pybin/chosen-python" ]]; then
    pass "honours ODS_PYTHON_CMD given as an absolute path"
else
    fail "honours ODS_PYTHON_CMD given as an absolute path"
    echo "    resolved: $resolved"
fi

resolved="$(PATH="$TMP_DIR/pybin:$PATH" ODS_PYTHON_CMD="chosen-python" _ods_compose_python_cmd)"
if [[ "$resolved" == "chosen-python" ]]; then
    pass "honours ODS_PYTHON_CMD given as a command name on PATH"
else
    fail "honours ODS_PYTHON_CMD given as a command name on PATH"
    echo "    resolved: $resolved"
fi

resolved="$(ODS_PYTHON_CMD="definitely-not-on-path-$$" _ods_compose_python_cmd || true)"
if [[ "$resolved" != "definitely-not-on-path-$$" ]]; then
    pass "falls back when ODS_PYTHON_CMD names nothing runnable"
else
    fail "falls back when ODS_PYTHON_CMD names nothing runnable"
    echo "    resolved: $resolved"
fi


out="$TMP_DIR/images.out"
ods_compose_external_images "$TMP_DIR/docker compose" -f docker-compose.base.yml > "$out"

echo ""
echo "=== Compose image discovery tests ==="
echo ""

assert_contains_line "$out" "ghcr.io/ggml-org/llama.cpp:server-b8248" "discovers remote base image"
assert_contains_line "$out" "caddy:2.11.3-alpine" "discovers extension remote image"
assert_contains_line "$out" "itzcrazykns1337/perplexica:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e" "preserves digest-pinned image"
assert_not_contains_line "$out" "ods-dashboard:latest" "skips services with local build image tags"
assert_not_contains_line "$out" "docker.io/library/ods-helper:latest" "skips generated local ODS image tags"

caddy_count="$(grep -Fx "caddy:2.11.3-alpine" "$out" | wc -l | tr -d '[:space:]')"
if [[ "$caddy_count" == "1" ]]; then
    pass "deduplicates repeated images"
else
    fail "deduplicates repeated images"
    echo "    caddy count: $caddy_count"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
