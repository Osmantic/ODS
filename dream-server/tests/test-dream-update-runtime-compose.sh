#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/dream-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
[[ -f "$UPDATE_SCRIPT" ]] || fail "dream-update.sh not found"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/dream-server"
BIN_DIR="$TMP_DIR/bin"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/config/litellm" "$BIN_DIR"

cp "$UPDATE_SCRIPT" "$INSTALL_DIR/dream-update.sh"
chmod +x "$INSTALL_DIR/dream-update.sh"

cat > "$INSTALL_DIR/.env" <<'EOF'
DREAM_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF

cat > "$INSTALL_DIR/.version" <<'EOF'
{"version":"test-runtime"}
EOF

cat > "$INSTALL_DIR/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
  litellm:
    image: example/litellm:test
EOF

cat > "$INSTALL_DIR/docker-compose.cpu.yml" <<'EOF'
services:
  llama-server:
    image: example/llama:test
EOF

printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$INSTALL_DIR/.compose-flags"

DOCKER_LOG="$TMP_DIR/docker-args.log"
export DOCKER_LOG
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"

if [[ "${1:-}" == "info" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
    shift
fi

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "ps" ]]; then
        next="${args[$((i + 1))]:-}"
        if [[ "$next" == "--services" ]]; then
            printf '%s\n' dashboard-api litellm
            exit 0
        fi
        if [[ "$next" == "--format" ]]; then
            printf '%s\n' '{"State":"running"}'
            exit 0
        fi
    fi
done

exit 0
SH
chmod +x "$BIN_DIR/docker"

cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$BIN_DIR/curl"

PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/dream-update.sh" health > "$TMP_DIR/health.out" 2>&1 \
    || { cat "$TMP_DIR/health.out"; fail "health should pass with saved compose flags"; }

grep -q "Service dashboard-api: running" "$TMP_DIR/health.out" \
    || { cat "$TMP_DIR/health.out"; fail "health did not inspect dashboard-api"; }
grep -q -- "-f docker-compose.base.yml -f docker-compose.cpu.yml ps --services" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "health did not pass saved compose flags to docker compose ps"; }
if grep -q "No services defined in docker-compose" "$TMP_DIR/health.out"; then
    cat "$TMP_DIR/health.out"
    fail "health emitted stale bare-compose warning"
fi
pass "dream-update health uses saved compose flags in runtime installs"

set +e
PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/dream-update.sh" update > "$TMP_DIR/update.out" 2>&1
update_exit=$?
set -e

[[ "$update_exit" -ne 0 ]] || fail "update without .git should fail"
grep -q "not a git repository" "$TMP_DIR/update.out" \
    || { cat "$TMP_DIR/update.out"; fail "missing non-git install diagnosis"; }
grep -q "./dream-cli update" "$TMP_DIR/update.out" \
    || { cat "$TMP_DIR/update.out"; fail "missing runtime update guidance"; }
grep -q "git-backed DreamServer checkout" "$TMP_DIR/update.out" \
    || { cat "$TMP_DIR/update.out"; fail "missing source update guidance"; }
pass "dream-update explains non-git runtime update path"
