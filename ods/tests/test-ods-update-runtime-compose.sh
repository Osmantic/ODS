#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v git >/dev/null 2>&1 || fail "git is required"
[[ -f "$UPDATE_SCRIPT" ]] || fail "ods-update.sh not found"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/ods"
BIN_DIR="$TMP_DIR/bin"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/config/litellm" "$BIN_DIR"

cp "$UPDATE_SCRIPT" "$INSTALL_DIR/ods-update.sh"
chmod +x "$INSTALL_DIR/ods-update.sh"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
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
if [[ "$*" == *"api.github.com/repos/"*"releases/latest"* ]]; then
    printf '%s\n' '{"tag_name":"v9.9.9"}'
fi
exit 0
SH
chmod +x "$BIN_DIR/curl"

PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/ods-update.sh" health > "$TMP_DIR/health.out" 2>&1 \
    || { cat "$TMP_DIR/health.out"; fail "health should pass with saved compose flags"; }

grep -q "Service dashboard-api: running" "$TMP_DIR/health.out" \
    || { cat "$TMP_DIR/health.out"; fail "health did not inspect dashboard-api"; }
grep -q -- "-f docker-compose.base.yml -f docker-compose.cpu.yml ps --services" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "health did not pass saved compose flags to docker compose ps"; }
if grep -q "No services defined in docker-compose" "$TMP_DIR/health.out"; then
    cat "$TMP_DIR/health.out"
    fail "health emitted stale bare-compose warning"
fi
pass "ods-update health uses saved compose flags in runtime installs"

PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/ods-update.sh" backup runtime-smoke > "$TMP_DIR/backup.out" 2>&1 \
    || { cat "$TMP_DIR/backup.out"; fail "backup should not fail while counting copied files"; }
grep -q "Backup created:" "$TMP_DIR/backup.out" \
    || { cat "$TMP_DIR/backup.out"; fail "backup did not report created snapshot"; }
pass "ods-update backup counts copied files under set -e"

cat > "$INSTALL_DIR/.version" <<'EOF'
{"version":"1.0.0"}
EOF
PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/ods-update.sh" check > "$TMP_DIR/check.out" 2>&1 \
    || { cat "$TMP_DIR/check.out"; fail "check should pass with mocked release API"; }
grep -q "ods update" "$TMP_DIR/check.out" \
    || { cat "$TMP_DIR/check.out"; fail "check did not recommend runtime update command"; }
if grep -q "Run 'ods-update.sh update' to update" "$TMP_DIR/check.out"; then
    cat "$TMP_DIR/check.out"
    fail "check still recommends direct source updater for runtime installs"
fi
pass "ods-update check recommends runtime update command"

SOURCE_BIN_DIR="$TMP_DIR/source-bin"
SOURCE_PARENT="$TMP_DIR/source-parent"
SOURCE_INSTALL="$SOURCE_PARENT/ods"
mkdir -p "$SOURCE_BIN_DIR" "$SOURCE_INSTALL"
cp "$UPDATE_SCRIPT" "$SOURCE_INSTALL/ods-update.sh"
chmod +x "$SOURCE_INSTALL/ods-update.sh"
cat > "$SOURCE_BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
if [[ "$*" == *"api.github.com/repos/"*"releases/latest"* ]]; then
    printf '%s\n' '{"tag_name":"v9.9.9"}'
fi
exit 0
SH
chmod +x "$SOURCE_BIN_DIR/curl"
git -C "$SOURCE_PARENT" init -q
git -C "$SOURCE_PARENT" add ods/ods-update.sh
PATH="$SOURCE_BIN_DIR:$PATH" bash "$SOURCE_INSTALL/ods-update.sh" check > "$TMP_DIR/source-check.out" 2>&1 \
    || { cat "$TMP_DIR/source-check.out"; fail "check should pass in nested source checkout"; }
grep -q "Source checkout detected" "$TMP_DIR/source-check.out" \
    || { cat "$TMP_DIR/source-check.out"; fail "nested source checkout was not recognized"; }
pass "ods-update recognizes nested source checkout layout"

: > "$DOCKER_LOG"
set +e
PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/ods-update.sh" update > "$TMP_DIR/update.out" 2>&1
update_exit=$?
set -e

[[ "$update_exit" -ne 0 ]] || fail "update without .git should fail"
grep -q "git-backed ODS source checkout" "$TMP_DIR/update.out" \
    || { cat "$TMP_DIR/update.out"; fail "missing non-git install diagnosis"; }
grep -q "./ods-cli update" "$TMP_DIR/update.out" \
    || { cat "$TMP_DIR/update.out"; fail "missing runtime update guidance"; }
if find "$INSTALL_DIR/data/backups" -mindepth 1 -maxdepth 1 -type d -name 'pre-update-*' 2>/dev/null | grep -q .; then
    find "$INSTALL_DIR/data/backups" -mindepth 1 -maxdepth 1 -type d -name 'pre-update-*'
    fail "non-git update created a rollback snapshot before preflight"
fi
[[ ! -s "$DOCKER_LOG" ]] || { cat "$DOCKER_LOG"; fail "non-git update invoked Docker"; }
pass "ods-update fails cleanly before mutating non-git runtime installs"
