#!/usr/bin/env bash
# Verifies that ods-update.sh rollback resolves the layered compose stack:
#   - `down` uses the currently active stack flags
#   - `up -d` uses the stack selected by the restored snapshot
#   - snapshots record .compose-flags; legacy snapshots without it fall back
#     to resolution from the restored .env instead of a stale cache
# Hermetic: docker is stubbed and records its arguments.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
[[ -f "$UPDATE_SCRIPT" ]] || fail "ods-update.sh not found"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_DIR="$TMP_DIR/bin"
mkdir -p "$BIN_DIR"

DOCKER_LOG=""
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

# make_install <dir> — install dir with an active nvidia stack
make_install() {
    local dir="$1"
    mkdir -p "$dir/data/backups" "$dir/data"
    cp "$UPDATE_SCRIPT" "$dir/ods-update.sh"
    chmod +x "$dir/ods-update.sh"

    cat > "$dir/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=nvidia
GPU_COUNT=1
TIER=1
EOF
    echo '{"version":"test-rollback"}' > "$dir/.version"

    cat > "$dir/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
EOF
    cat > "$dir/docker-compose.nvidia.yml" <<'EOF'
services:
  llama-server:
    image: example/llama:nvidia-test
EOF
    cat > "$dir/docker-compose.cpu.yml" <<'EOF'
services:
  llama-server:
    image: example/llama:cpu-test
EOF
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' \
        > "$dir/.compose-flags"
}

# make_snapshot <install_dir> <ts> <with_flags> — cpu-stack snapshot
make_snapshot() {
    local dir="$1" ts="$2" with_flags="$3"
    local snap="$dir/data/backups/pre-update-$ts"
    mkdir -p "$snap"

    cat > "$snap/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
EOF
    echo '{"version":"pre-rollback"}' > "$snap/.version"
    cp "$dir/docker-compose.base.yml" "$snap/"
    cp "$dir/docker-compose.cpu.yml" "$snap/"

    if [[ "$with_flags" == "yes" ]]; then
        printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' \
            > "$snap/.compose-flags"
    fi

    jq -n --arg ts "$ts" \
        '{type:"pre-update", timestamp:$ts, version:"pre-rollback", files_count:4, install_dir:"test"}' \
        > "$snap/snapshot.json"
}

DOWN_NVIDIA='compose -f docker-compose.base.yml -f docker-compose.nvidia.yml down'
UP_CPU='compose -f docker-compose.base.yml -f docker-compose.cpu.yml up -d'

# ── Scenario 1: snapshot carries .compose-flags ──────────────────────────────
INSTALL_A="$TMP_DIR/ods-a"
make_install "$INSTALL_A"
make_snapshot "$INSTALL_A" "20260101-000000" "yes"

DOCKER_LOG="$TMP_DIR/docker-a.log"
: > "$DOCKER_LOG"
PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=30 \
    bash "$INSTALL_A/ods-update.sh" rollback 20260101-000000 \
    > "$TMP_DIR/rollback-a.out" 2>&1 \
    || { cat "$TMP_DIR/rollback-a.out"; fail "rollback (snapshot with flags) should succeed"; }

grep -qxF "$DOWN_NVIDIA" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "down should use the active nvidia stack flags"; }
pass "down used the active layered stack (base + nvidia)"

grep -qxF "$UP_CPU" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "up should use the restored cpu stack flags"; }
pass "up used the restored layered stack (base + cpu)"

grep -qF -- '-f docker-compose.cpu.yml' "$INSTALL_A/.compose-flags" \
    || fail ".compose-flags should be restored from the snapshot"
pass ".compose-flags restored from snapshot"

# ── Scenario 2: legacy snapshot without .compose-flags ───────────────────────
INSTALL_B="$TMP_DIR/ods-b"
make_install "$INSTALL_B"
make_snapshot "$INSTALL_B" "20260102-000000" "no"

DOCKER_LOG="$TMP_DIR/docker-b.log"
: > "$DOCKER_LOG"
PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=30 \
    bash "$INSTALL_B/ods-update.sh" rollback 20260102-000000 \
    > "$TMP_DIR/rollback-b.out" 2>&1 \
    || { cat "$TMP_DIR/rollback-b.out"; fail "rollback (legacy snapshot) should succeed"; }

grep -qxF "$DOWN_NVIDIA" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "down should use the active nvidia stack flags (legacy)"; }
pass "legacy: down used the active layered stack (base + nvidia)"

grep -qxF "$UP_CPU" "$DOCKER_LOG" \
    || { cat "$DOCKER_LOG"; fail "up should resolve the cpu stack from the restored .env, not the stale cache"; }
pass "legacy: up resolved base + cpu from the restored .env"

[[ ! -f "$INSTALL_B/.compose-flags" ]] \
    || fail "stale .compose-flags should be cleared when the snapshot has none"
pass "legacy: stale .compose-flags cleared"

# ── Scenario 3: pre-update snapshot records .compose-flags ───────────────────
INSTALL_C="$TMP_DIR/ods-c"
make_install "$INSTALL_C"

# `main help` prints usage and returns, leaving the functions loaded
snap_dir="$(cd "$INSTALL_C" && PATH="$BIN_DIR:$PATH" bash -c '
    source ./ods-update.sh help >/dev/null
    snapshot_pre_update 20260103-000000
' 2>/dev/null | tail -1)"

[[ -n "$snap_dir" && -d "$snap_dir" ]] || fail "snapshot_pre_update did not return a snapshot dir"
[[ -f "$snap_dir/.compose-flags" ]] || fail "snapshot should include .compose-flags"
grep -qF -- '-f docker-compose.nvidia.yml' "$snap_dir/.compose-flags" \
    || fail "snapshotted .compose-flags should record the active nvidia stack"
pass "snapshot_pre_update saved .compose-flags"

echo ""
echo "All rollback compose stack tests passed."
