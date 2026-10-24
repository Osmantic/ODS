#!/usr/bin/env bash
# Regression (#7645): a bootstrap upgrade cut off by a host reboot between the
# llama-server recreate and its start must not leave llama-server "Created",
# bootstrap-status.json "swapping" and a dead task "running" forever, and
# `ods status` must show the unstarted container instead of a fake upgrade.
# The run is handed to the start/restart resume, never marked complete: only
# bootstrap-upgrade.sh finishes Pixel, Hermes, Perplexica, cleanup and the
# host-agent notice.

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
HELP_LINK="https://discord.gg/4ntNp9MAwC"

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
    info)
        [[ "${FAKE_DOCKER_DOWN:-0}" != 1 ]] || exit 1
        echo "8"
        ;;
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
cat > "$BIN_DIR/uname" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-s" && -n "${FAKE_UNAME:-}" ]]; then
    echo "$FAKE_UNAME"
    exit 0
fi
for real_uname in /bin/uname /usr/bin/uname; do
    [[ -x "$real_uname" ]] && exec "$real_uname" "$@"
done
exit 127
SH
chmod +x "$BIN_DIR/docker" "$BIN_DIR/ps" "$BIN_DIR/curl" "$BIN_DIR/uname"

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

# The reconcile hands the run to the resume: failed, with an interrupted
# message that names the recovery step and the help link.
expect_handed_to_resume() {
    local label="$1" eta
    [[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == failed ]] \
        || fail "$label: status is not failed"
    eta="$(json_get "$INSTALL_DIR/data/bootstrap-status.json" eta)"
    [[ "$eta" == interrupted:* ]] || fail "$label: status message does not say interrupted: $eta"
    [[ "$eta" == *"ods start"* ]] || fail "$label: status message gives no recovery step: $eta"
    [[ "$eta" == *"$HELP_LINK"* ]] || fail "$label: status message gives no help link: $eta"
    [[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" bytesTotal)" == 100 ]] \
        || fail "$label: status lost its download fields"
    [[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == interrupted ]] \
        || fail "$label: dead background task still recorded as running"
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

# 2. Stale swapping + dead pid + Created container, .env already promoted:
#    start the container, hand the run to the resume, never write complete.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=created FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
grep -q 'DOCKER_ARGS: start ods-llama-server' "$DOCKER_LOG" || fail "Created llama-server was not started"
expect_handed_to_resume "interrupted recreate"
if [[ "$(uname -s)" == Linux ]]; then
    [[ "$(stat -c '%a' "$INSTALL_DIR/data/bootstrap-status.json")" == 644 ]] \
        || fail "status file is not readable to the Dashboard API (want 0644, like bootstrap-upgrade.sh)"
fi
pass "interrupted swap: container started, run handed to the resume, task interrupted"

# 2b. Even a fully promoted, running and healthy full model is not complete:
#     Pixel, Hermes, Perplexica, cleanup and the host-agent notice never ran.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=running FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "a running llama-server was started again"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" != complete ]] \
    || fail "the reconcile wrote complete and skipped bootstrap-upgrade.sh's remaining steps"
expect_handed_to_resume "promoted and serving"
pass "promoted and serving: still handed to the resume, never complete"

# 3. Not yet promoted, container exited (maybe `ods stop`) -> left alone,
#    run handed to the resume.
write_fixture "$BOOT_MODEL"
FAKE_LLAMA_STATE=exited run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "exited llama-server was started against a possible ods stop"
expect_handed_to_resume "exited container"
pass "exited container: not started, run handed to the resume, task settled"

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
expect_handed_to_resume "no container"
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

# 3e. Docker not up yet (early boot): deferred for a later recheck, not failed.
write_fixture "$FULL_MODEL"
FAKE_DOCKER_DOWN=1 FAKE_LLAMA_STATE=created EXPECT_RC=75 run_reconcile
grep -q 'DOCKER_ARGS: start' "$DOCKER_LOG" && fail "reconcile started llama-server while Docker was down"
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == swapping ]] \
    || fail "reconcile settled the status while Docker was down"
[[ "$(json_get "$INSTALL_DIR/logs/background-tasks.json" status)" == running ]] \
    || fail "reconcile rewrote the task record while Docker was down"
pass "Docker not answering: untouched, deferred (75)"

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

# 4b. macOS native llama: the Docker container is not the runtime, so a stale
# status is left to ods-macos.sh instead of being marked failed.
write_fixture "$FULL_MODEL"
sed -i.bak 's/^GPU_BACKEND=nvidia$/GPU_BACKEND=apple/' "$INSTALL_DIR/.env"
FAKE_UNAME=Darwin FAKE_LLAMA_STATE= run_reconcile
[[ "$(json_get "$INSTALL_DIR/data/bootstrap-status.json" status)" == swapping ]]     || fail "reconcile rewrote the status on a macOS native-llama install"
grep -q 'DOCKER_ARGS' "$DOCKER_LOG" && fail "reconcile queried Docker on a macOS native-llama install"
pass "macOS native llama: status left to ods-macos.sh"

# 5. `ods status` flags the unstarted container and hides the dead upgrade line.
write_fixture "$FULL_MODEL"
PATH="$BIN_DIR:$PATH" ODS_HOME="$INSTALL_DIR" FAKE_LLAMA_STATE=created \
    "$BASH" "$INSTALL_DIR/ods-cli" status > "$OUT" 2>&1 || true # status exit code reflects stub health checks
grep -q "llama-server: container created but not started" "$OUT" \
    || fail "ods status did not report the Created llama-server container"
grep -q "downloaded)" "$OUT" && fail "ods status still prints a progress line for a dead upgrade"
grep -q "Model Upgrade: $FULL_MODEL interrupted" "$OUT" \
    || fail "ods status did not mark the dead upgrade as interrupted"
grep -qF "$HELP_LINK" "$OUT" || fail "ods status gave no help link for the interrupted upgrade"
pass "ods status reports the Created container and the interrupted upgrade"

# 5b. After the reconcile the run is failed (interrupted); status still says
#     how to finish it until the resume has done so.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=created FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
PATH="$BIN_DIR:$PATH" ODS_HOME="$INSTALL_DIR" FAKE_LLAMA_STATE=running \
    "$BASH" "$INSTALL_DIR/ods-cli" status > "$OUT" 2>&1 || true # status exit code reflects stub health checks
grep -q "Model Upgrade: $FULL_MODEL interrupted before it finished — run 'ods start' to finish it" "$OUT" \
    || fail "ods status hid the interrupted upgrade once it was marked failed"
grep -qF "$HELP_LINK" "$OUT" || fail "ods status gave no help link for the interrupted upgrade"
pass "ods status keeps naming the interrupted upgrade and its recovery step"

# 6. The existing start/restart resume picks the reconciled run up and starts
#    bootstrap-upgrade.sh again with the saved arguments.
write_fixture "$FULL_MODEL"
FAKE_LLAMA_STATE=created FAKE_LLAMA_CMD="--model /models/$FULL_MODEL" run_reconcile
printf '%s\n' "$FULL_MODEL" "https://example.invalid/full.gguf" "abc123" "full-model" "65536" "$BOOT_MODEL" \
    > "$INSTALL_DIR/data/bootstrap-upgrade.args"
export RESUME_ARGS_FILE="$TMP/resume-args"
rm -f "$RESUME_ARGS_FILE"
cat > "$INSTALL_DIR/scripts/bootstrap-upgrade.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "${RESUME_ARGS_FILE:?}"
SH
resume_fn="$TMP/resume-fn.sh"
awk '/^_ods_cli_maybe_resume_bootstrap_upgrade\(\) \{$/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$ROOT_DIR/ods-cli" > "$resume_fn"
[[ -s "$resume_fn" ]] || fail "could not extract _ods_cli_maybe_resume_bootstrap_upgrade from ods-cli"
(
    log() { printf '%s\n' "$*"; }
    warn() { printf 'WARN %s\n' "$*"; }
    # shellcheck source=/dev/null
    . "$resume_fn"
    _ods_cli_maybe_resume_bootstrap_upgrade
) > "$OUT" 2>&1
for _ in $(seq 1 50); do
    [[ -s "$RESUME_ARGS_FILE" ]] && break
    sleep 0.1
done
[[ -s "$RESUME_ARGS_FILE" ]] || fail "the start/restart resume did not restart bootstrap-upgrade.sh for the interrupted run"
[[ "$(sed -n '1p;2p;7p' "$RESUME_ARGS_FILE" | paste -sd ' ' -)" == "$INSTALL_DIR $FULL_MODEL $BOOT_MODEL" ]] \
    || fail "the resume did not pass the saved upgrade arguments: $(paste -sd ' ' - < "$RESUME_ARGS_FILE")"
grep -q "previous upgrade was interrupted; retrying in background" "$OUT" \
    || fail "the resume called an interrupted upgrade a failed download"
pass "start/restart resume restarts bootstrap-upgrade.sh for the interrupted run"

echo "All bootstrap swap reconciliation checks passed."
