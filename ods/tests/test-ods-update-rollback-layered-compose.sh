#!/usr/bin/env bash
# test-ods-update-rollback-layered-compose.sh
#
# Regression test for the "rollback ignores layered compose stack" bug.
#
# Scenario (from the issue):
#   Current stack : docker-compose.base.yml + docker-compose.nvidia.yml
#   Snapshot stack: docker-compose.base.yml + docker-compose.cpu.yml
#   No default docker-compose.yml exists.
#
# cmd_rollback() must:
#   1. Resolve the CURRENT stack and pass those -f flags to `docker compose down`
#      (so services actually stop on installs with no default docker-compose.yml).
#   2. RE-resolve after restoring the snapshot and pass the NEW -f flags to
#      `docker compose up -d` (the restored .env may pick a different backend).
#
# Before the fix, both invocations were bare (`docker compose down`,
# `docker compose up -d`) and failed with "no configuration file provided".

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
[[ -f "$UPDATE_SCRIPT" ]] || fail "ods-update.sh not found"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/ods"
BIN_DIR="$TMP_DIR/bin"
mkdir -p "$INSTALL_DIR/data/backups" "$BIN_DIR"

cp "$UPDATE_SCRIPT" "$INSTALL_DIR/ods-update.sh"
chmod +x "$INSTALL_DIR/ods-update.sh"

# ─── Current install: base + nvidia, GPU_BACKEND=nvidia ─────────────────────
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=nvidia
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF

cat > "$INSTALL_DIR/.version" <<'EOF'
{"version":"2.0.0"}
EOF

cat > "$INSTALL_DIR/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
EOF

cat > "$INSTALL_DIR/docker-compose.nvidia.yml" <<'EOF'
services:
  llama-server:
    image: example/llama-nvidia:test
EOF

# Cached flags for the currently-running stack. cmd_rollback() must use these
# for `down`, not fall back to a bare `docker compose down`.
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' \
    > "$INSTALL_DIR/.compose-flags"

# NOTE: No `docker-compose.yml` — this is the exact repro condition from
# the issue. A bare `docker compose down` would fail with "no configuration
# file provided: not found".
[[ ! -f "$INSTALL_DIR/docker-compose.yml" ]] || fail "test setup left a stray docker-compose.yml"

# ─── Snapshot: same install BUT resolved to base + cpu, GPU_BACKEND=cpu ─────
SNAP_TS="20260101-120000"
SNAP_DIR="$INSTALL_DIR/data/backups/pre-update-$SNAP_TS"
mkdir -p "$SNAP_DIR"

cat > "$SNAP_DIR/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=0
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF

cat > "$SNAP_DIR/.version" <<'EOF'
{"version":"1.0.0"}
EOF

# The snapshot only carries base + cpu — the restored install must not run
# with the nvidia overlay still attached.
cp "$INSTALL_DIR/docker-compose.base.yml" "$SNAP_DIR/docker-compose.base.yml"
cat > "$SNAP_DIR/docker-compose.cpu.yml" <<'EOF'
services:
  llama-server:
    image: example/llama-cpu:test
EOF

jq -n \
    --arg ts  "2026-01-01T12:00:00Z" \
    --arg ver "1.0.0" \
    --argjson fc 4 \
    --arg dir "$INSTALL_DIR" \
    '{type:"pre-update", timestamp:$ts, version:$ver, files_count:$fc, install_dir:$dir}' \
    > "$SNAP_DIR/snapshot.json"

# ─── Stubbed docker: logs args, returns success, fakes ps ────────────────────
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

# Strip the leading `compose` verb so we can scan the remaining args.
if [[ "${1:-}" == "compose" ]]; then
    shift
fi

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "ps" ]]; then
        next="${args[$((i + 1))]:-}"
        if [[ "$next" == "--services" ]]; then
            printf '%s\n' dashboard-api llama-server
            exit 0
        fi
        if [[ "$next" == "--format" ]]; then
            printf '%s\n' '{"State":"running"}'
            exit 0
        fi
    fi
done

# `down` / `up -d` / everything else: succeed silently.
exit 0
SH
chmod +x "$BIN_DIR/docker"

# curl stub — cmd_health probes /health and /v1/models via curl.
cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$BIN_DIR/curl"

# ─── Run rollback ────────────────────────────────────────────────────────────
: > "$DOCKER_LOG"
set +e
PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/ods-update.sh" rollback "$SNAP_TS" \
    > "$TMP_DIR/rollback.out" 2>&1
rollback_exit=$?
set -e

if [[ "$rollback_exit" -ne 0 ]]; then
    echo "=== rollback.out ==="
    cat "$TMP_DIR/rollback.out"
    echo "=== docker-args.log ==="
    cat "$DOCKER_LOG"
    fail "rollback exited $rollback_exit (expected 0 with stubbed docker)"
fi
pass "rollback command completed successfully"

# ─── Assertions on docker invocations ────────────────────────────────────────
if ! grep -qE '^compose (.* )?-f docker-compose\.base\.yml -f docker-compose\.nvidia\.yml down( |$)' "$DOCKER_LOG"; then
    echo "=== docker-args.log ==="
    cat "$DOCKER_LOG"
    fail "rollback did not pass current stack flags (base+nvidia) to docker compose down"
fi
pass "rollback down used the pre-restore stack flags (base+nvidia)"

if ! grep -qE '^compose (.* )?-f docker-compose\.base\.yml -f docker-compose\.cpu\.yml up -d( |$)' "$DOCKER_LOG"; then
    echo "=== docker-args.log ==="
    cat "$DOCKER_LOG"
    fail "rollback did not pass restored stack flags (base+cpu) to docker compose up -d"
fi
pass "rollback up -d used the restored stack flags (base+cpu)"

# Also assert the bug is really gone — no bare `compose down` / `compose up -d`
# (without any -f flag) during the rollback path.
if grep -qE '^compose down( |$)' "$DOCKER_LOG"; then
    echo "=== docker-args.log ==="
    cat "$DOCKER_LOG"
    fail "rollback invoked bare 'docker compose down' — regression of the bug"
fi
if grep -qE '^compose up -d( |$)' "$DOCKER_LOG"; then
    echo "=== docker-args.log ==="
    cat "$DOCKER_LOG"
    fail "rollback invoked bare 'docker compose up -d' — regression of the bug"
fi
pass "rollback did not fall back to bare compose commands"

# ─── Post-restore state ──────────────────────────────────────────────────────
grep -q '^GPU_BACKEND=cpu$' "$INSTALL_DIR/.env" \
    || { cat "$INSTALL_DIR/.env"; fail ".env was not restored (GPU_BACKEND should be cpu)"; }
pass ".env restored from snapshot (GPU_BACKEND=cpu)"

# The stale .compose-flags cache pointed to base+nvidia; cmd_rollback must
# clear it so subsequent health/updates re-resolve against the restored env.
if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
    cache="$(cat "$INSTALL_DIR/.compose-flags")"
    fail ".compose-flags cache was not invalidated after restore (still: ${cache})"
fi
pass ".compose-flags cache invalidated after restore"

[[ "$(jq -r '.version' "$INSTALL_DIR/.version")" == "1.0.0" ]] \
    || fail ".version was not restored"
pass ".version restored from snapshot (1.0.0)"

# ─── ods-restore.sh stop_containers: same bare-compose class of bug ─────────
# Verify ods-restore.sh (a separate script sharing the same install layout)
# also passes resolved -f flags to `docker compose down` on layered installs.
RESTORE_SCRIPT="$ROOT_DIR/ods-restore.sh"
if [[ -f "$RESTORE_SCRIPT" ]]; then
    RESTORE_DIR="$TMP_DIR/ods-restore-target"
    mkdir -p "$RESTORE_DIR/lib"
    # Strip the trailing `main "$@"` so we can source stop_containers alone.
    sed -e 's|^main "\$@"$|:|' "$RESTORE_SCRIPT" > "$RESTORE_DIR/ods-restore.sh"
    chmod +x "$RESTORE_DIR/ods-restore.sh"
    # ods-restore.sh sources lib/rsync.sh — provide a no-op stub.
    if [[ -f "$ROOT_DIR/lib/rsync.sh" ]]; then
        cp "$ROOT_DIR/lib/rsync.sh" "$RESTORE_DIR/lib/rsync.sh"
    else
        cat > "$RESTORE_DIR/lib/rsync.sh" <<'EOF'
rsync_with_progress() { cp -r "$1" "$2"; }
EOF
    fi

    cat > "$RESTORE_DIR/.env" <<'EOF'
GPU_BACKEND=nvidia
GPU_COUNT=1
TIER=1
ODS_MODE=local
EOF
    cat > "$RESTORE_DIR/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
EOF
    cat > "$RESTORE_DIR/docker-compose.nvidia.yml" <<'EOF'
services:
  llama-server:
    image: example/llama-nvidia:test
EOF
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' \
        > "$RESTORE_DIR/.compose-flags"
    [[ ! -f "$RESTORE_DIR/docker-compose.yml" ]] \
        || fail "test setup left a stray docker-compose.yml in restore dir"

    # Stub docker again with a fresh log. compose ls must report a project
    # matching basename($RESTORE_DIR) so stop_containers doesn't early-return.
    RESTORE_DOCKER_LOG="$TMP_DIR/docker-restore-args.log"
    RESTORE_PROJECT_BASENAME="$(basename "$RESTORE_DIR")"
    export RESTORE_DOCKER_LOG RESTORE_PROJECT_BASENAME
    cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${RESTORE_DOCKER_LOG:?}"

if [[ "${1:-}" == "info" ]]; then exit 0; fi
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then exit 0; fi

if [[ "${1:-}" == "compose" ]]; then shift; fi

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "ls" ]]; then
        printf '%s\n' "${RESTORE_PROJECT_BASENAME}"
        exit 0
    fi
done

exit 0
SH
    chmod +x "$BIN_DIR/docker"

    : > "$RESTORE_DOCKER_LOG"
    # Drive stop_containers directly by sourcing the script's helpers.
    # We can't just run the script because it needs a real backup archive;
    # sourcing lets us call stop_containers() in isolation.
    (
        cd "$RESTORE_DIR"
        export ODS_DIR="$RESTORE_DIR"
        export PATH="$BIN_DIR:$PATH"
        # main "$@" was sed'd out above so sourcing only loads functions.
        # shellcheck disable=SC1091
        source "$RESTORE_DIR/ods-restore.sh"
        stop_containers
    ) > "$TMP_DIR/restore-stop.out" 2>&1 || {
        cat "$TMP_DIR/restore-stop.out"
        fail "ods-restore.sh stop_containers exited non-zero"
    }

    if ! grep -qE '^compose (.* )?-f docker-compose\.base\.yml -f docker-compose\.nvidia\.yml down( |$)' "$RESTORE_DOCKER_LOG"; then
        echo "=== docker-restore-args.log ==="
        cat "$RESTORE_DOCKER_LOG"
        fail "ods-restore.sh stop_containers did not pass resolved -f flags to docker compose down"
    fi
    pass "ods-restore.sh stop_containers uses resolved -f flags on layered installs"

    if grep -qE '^compose down( |$)' "$RESTORE_DOCKER_LOG"; then
        echo "=== docker-restore-args.log ==="
        cat "$RESTORE_DOCKER_LOG"
        fail "ods-restore.sh stop_containers invoked bare 'docker compose down' — regression"
    fi
    pass "ods-restore.sh stop_containers did not fall back to bare compose down"
fi

echo ""
echo "All rollback layered-compose checks passed."
