#!/usr/bin/env bash
# Regression (#7645): a bootstrap upgrade cut off by a host reboot between the
# llama-server recreate and its start must not leave llama-server "Created",
# bootstrap-status.json "swapping" and a dead task "running" forever, and
# `ods status` must show the unstarted container instead of a fake upgrade.

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-cli requires Bash 4+\n'
    exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FULL_MODEL="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
BOOT_MODEL="Qwen3.5-2B-Q4_K_M.gguf"

pass() { printf '  PASS %s\n' "$1"; }
fail() {
    printf '  FAIL %s\n' "$1" >&2
    [[ -f "${OUT:-}" ]] && sed 's/^/[out] /' "$OUT" >&2
    [[ -f "${DOCKER_LOG:-}" ]] && sed 's/^/[docker] /' "$DOCKER_LOG" >&2
    exit 1
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ods-bootstrap-reconcile.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

INSTALL_DIR="$TMP/install"
BIN_DIR="$TMP/bin"
OUT="$TMP/out"
DOCKER_LOG="$TMP/docker.log"
STARTED_FLAG="$TMP/started"
export DOCKER_LOG STARTED_FLAG
export ODS_BOOTSTRAP_RECONCILE_HEALTH_ATTEMPTS=2 ODS_BOOTSTRAP_RECONCILE_HEALTH_INTERVAL=0
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/scripts" \
    "$INSTALL_DIR/installers/lib" "$INSTALL_DIR/lib" "$BIN_DIR"
cp "$ROOT_DIR/scripts/reconcile-bootstrap-swap.sh" "$INSTALL_DIR/scripts/" \
    || fail "scripts/reconcile-bootstrap-swap.sh is missing"
cp "$ROOT_DIR/installers/lib/background-tasks.sh" "$INSTALL_DIR/installers/lib/"
cp "$ROOT_DIR/installers/lib/model-lifecycle-lock.sh" "$INSTALL_DIR/installers/lib/"
export ODS_MODEL_LIFECYCLE_LOCK_ROOT="$TMP/locks"
cp "$ROOT_DIR/ods-cli" "$INSTALL_DIR/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$INSTALL_DIR/lib/"
: > "$INSTALL_DIR/docker-compose.base.yml"

cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
echo "DOCKER_ARGS: $*" >> "${DOCKER_LOG:?}"
state="${FAKE_LLAMA_STATE:-}"
[[ -f "$STARTED_FLAG" ]] && state=running
case "${1:-}" in
    ps)
        [[ " $* " == *" -a "* ]] && printf '%s\n' "$state"
        ;;
    start) : > "$STARTED_FLAG" ;;
    inspect) printf '%s\n' "${FAKE_LLAMA_CMD:-}" ;;
    compose)
        [[ " $* " == *" ps -a "* && " $* " == *" llama-server"* ]] \
            && printf '%s\n' "${FAKE_LLAMA_STATE:-}"
        ;;
    info) echo "8" ;;
esac
exit 0
SH
cat > "$BIN_DIR/ps" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-eo" && "${2:-}" == "args=" ]]; then
    [[ -n "${FAKE_UPGRADE_ARGS:-}" ]] && echo "$FAKE_UPGRADE_ARGS"
    exit 0
fi
exec /bin/ps "$@"
SH
cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
echo "CURL_ARGS: $*" >> "${DOCKER_LOG:?}"
[[ "${FAKE_HEALTH:-ok}" == ok ]]
SH
chmod +x "$BIN_DIR/docker" "$BIN_DIR/ps" "$BIN_DIR/curl"

bash -c 'exit 0' &
DEAD_PID=$!
wait "$DEAD_PID"

write_fixture() {
    local env_model="$1" updated_at="${2:-2026-10-01T00:00:00Z}" ini_model="${3:-$1}"
    mkdir -p "$INSTALL_DIR/config/llama-server"
    printf '[m]\nfilename = %s\nload-on-startup = true\n' "$ini_model" \
        > "$INSTALL_DIR/config/llama-server/models.ini"
    rm -f "$STARTED_FLAG"
    cat > "$INSTALL_DIR/.env" <<EOF
ODS_VERSION=2.0.0
GPU_BACKEND=nvidia
TIER=4
GGUF_FILE=$env_model
SHIELD_API_KEY=test-fixture-key
EOF
    cat > "$INSTALL_DIR/data/bootstrap-status.json" <<EOF
{"status": "swapping", "model": "$FULL_MODEL", "percent": 100, "bytesDownloaded": 100, "bytesTotal": 100, "speedBytesPerSec": 0, "eta": "", "updatedAt": "$updated_at"}
EOF
    cat > "$INSTALL_DIR/logs/background-tasks.json" <<EOF
[{"id": "full-model-download", "pid": $DEAD_PID, "description": "Full model download", "log_file": "x", "status": "running"}]
EOF
    : > "$DOCKER_LOG"
}

json_get() {
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d=d[0] if isinstance(d,list) else d; print(d[sys.argv[2]])' "$1" "$2"
}

run_reconcile() {
    local expected_rc="${EXPECT_RC:-0}" rc=0
    PATH="$BIN_DIR:$PATH" bash "$INSTALL_DIR/scripts/reconcile-bootstrap-swap.sh" "$INSTALL_DIR" > "$OUT" 2>&1 \
        || rc=$?
    [[ "$rc" == "$expected_rc" ]] || fail "reconcile script exited $rc, expected $expected_rc"
}

echo "=== bootstrap swap reconciliation (#7645) ==="

# 1. Same-file short-circuit: no background swap for the bootstrap GGUF.
(
    tier_rank() { echo 3; }
    TIER=3 INSTALL_DIR="$TMP/none" NO_BOOTSTRAP=false OFFLINE_MODE=false ODS_MODE=local
    # shellcheck source=../installers/lib/bootstrap-model.sh
    . "$ROOT_DIR/installers/lib/bootstrap-model.sh"
    GGUF_FILE="$BOOTSTRAP_GGUF_FILE"
    if bootstrap_needed; then exit 1; fi
    GGUF_FILE="$FULL_MODEL"
    bootstrap_needed
) || fail "bootstrap_needed must skip the swap only when the full GGUF is the bootstrap GGUF"
pass "same-file full model skips the bootstrap swap"

# 2. Stale swapping + dead pid + Created container, .env already promoted.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=created FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
grep -q 'DOCKER_ARGS: start ods-llama-server' "$DOCKER_LOG" || fail "Created llama-server was not started"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == complete ]] \
    || fail "promoted swap was not marked complete"
[[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == interrupted ]] \
    || fail "dead background task still recorded as running"
pass "interrupted swap: container started, status complete, task interrupted"

# 2b. Mid-promotion interruption (.env written, models.ini and the runtime not
#     yet swapped): .env alone is not completion evidence -> failed, resumable.
write_fixture "$FULL_MODEL" "" "$BOOT_MODEL"
FAKE_LLAMA_STATE=running FAKE_LLAMA_CMD="--model /models/$BOOT_MODEL" run_reconcile
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]] \
    || fail "mid-promotion interruption was recorded complete from .env alone"
pass "mid-promotion (.env only): status failed, resumable"

# 2c. Config promoted but the running container still serves the bootstrap GGUF.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=running FAKE_LLAMA_CMD="--model /models/$BOOT_MODEL" run_reconcile
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]] \
    || fail "stale runtime --model was recorded complete"
pass "config promoted, runtime stale: status failed"

# 2d. Runtime selected but never healthy -> failed.
write_fixture "$FULL_MODEL"
FAKE_HEALTH=down FAKE_LLAMA_STATE=running FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]] \
    || fail "unhealthy runtime was recorded complete"
pass "runtime not healthy: status failed"

# 3. Not yet promoted, container exited (maybe `ods stop`) -> left alone,
#    status settled failed/interrupted.
write_fixture "$BOOT_MODEL"
FAKE_LLAMA_STATE=exited run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "exited llama-server was started against a possible ods stop"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]]     || fail "unpromoted swap was not marked failed"
grep -q 'interrupted' "$INSTALL_DIR/data/bootstrap-status.json" || fail "failed status lacks interrupted message"
[[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == interrupted ]]     || fail "dead background task still recorded as running (exited case)"
pass "exited container: not started, status failed (interrupted), task settled"

# 3b. Fresh active status, no visible process (installer child not in ps yet).
write_fixture "$BOOT_MODEL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
FAKE_LLAMA_STATE=created EXPECT_RC=75 run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "reconcile started llama-server for a fresh status"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == swapping ]]     || fail "reconcile settled a fresh active status"
[[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == running ]]     || fail "reconcile rewrote the task record for a fresh status"
pass "fresh active status without a process: untouched, deferred (75)"

# 3c. Stale active status, no process, no container -> settled.
write_fixture "$BOOT_MODEL"
FAKE_LLAMA_STATE="" run_reconcile
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]]     || fail "stale active status was not settled"
pass "stale active status without a process: settled"

# 3d. Model lifecycle lock held by another operation -> no action.
if [[ "$(uname -s)" == "Linux" ]] && command -v flock >/dev/null 2>&1; then
    write_fixture "$FULL_MODEL"
    # shellcheck source=../installers/lib/model-lifecycle-lock.sh
    lock_file="$(. "$ROOT_DIR/installers/lib/model-lifecycle-lock.sh"; ods_model_lifecycle_lock_file "$INSTALL_DIR")"
    mkdir -p "$(dirname "$lock_file")"
    exec {held_fd}>"$lock_file"
    flock -x "$held_fd"
    FAKE_LLAMA_STATE=created EXPECT_RC=75 run_reconcile
    exec {held_fd}>&-
    grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "reconcile started llama-server while the lifecycle lock was held"
    [[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == swapping ]]         || fail "reconcile rewrote status while the lifecycle lock was held"
    pass "lifecycle lock busy: no action, deferred (75)"
fi

# 4. Live upgrade process: nothing is touched.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=created \
FAKE_UPGRADE_ARGS="bash $INSTALL_DIR/scripts/bootstrap-upgrade.sh $INSTALL_DIR $FULL_MODEL url sha" \
    run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "reconcile started llama-server during a live upgrade"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == swapping ]] \
    || fail "reconcile rewrote status during a live upgrade"
[[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == running ]] \
    || fail "reconcile rewrote the task record during a live upgrade"
pass "live upgrade process: no action"

# 5. `ods status` flags the unstarted container and hides the dead upgrade line.
write_fixture "$FULL_MODEL"
PATH="$BIN_DIR:$PATH" ODS_HOME="$INSTALL_DIR" FAKE_LLAMA_STATE=created \
    timeout 120 "$BASH" "$INSTALL_DIR/ods-cli" status > "$OUT" 2>&1 || true # status exit code reflects stub health checks
grep -q "llama-server: container created but not started" "$OUT" \
    || fail "ods status did not report the Created llama-server container"
grep -q "downloaded)" "$OUT" && fail "ods status still prints a progress line for a dead upgrade"
grep -q "Model Upgrade: $FULL_MODEL interrupted" "$OUT" \
    || fail "ods status did not mark the dead upgrade as interrupted"
pass "ods status reports the Created container and the interrupted upgrade"

echo "All bootstrap swap reconciliation checks passed."
