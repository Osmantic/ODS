#!/usr/bin/env bash
# Boundary tests for application-consistent `ods-backup.sh` transactions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_BACKUP="$SCRIPT_DIR/../ods-backup.sh"
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }

FAKE_ODS="$TMP_ROOT/ods"
FAKE_BIN="$TMP_ROOT/bin"
mkdir -p "$FAKE_ODS/data/open-webui" "$FAKE_ODS/lib" "$FAKE_BIN"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$FAKE_ODS/lib/"
# PR #2190 centralizes the data path list in this optional sibling. Copy it
# when present so this boundary test composes with that independent change.
if [[ -f "$SCRIPT_DIR/../lib/backup-paths.sh" ]]; then
    cp "$SCRIPT_DIR/../lib/backup-paths.sh" "$FAKE_ODS/lib/"
fi
printf '{"version":"test"}\n' > "$FAKE_ODS/.version"
printf 'operator data\n' > "$FAKE_ODS/data/open-webui/value.txt"

DOCKER_LOG="$TMP_ROOT/docker.log"
RUNNING_FILE="$TMP_ROOT/running"
RSYNC_LOG="$TMP_ROOT/rsync.log"
export DOCKER_LOG RUNNING_FILE RSYNC_LOG

cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
    ps)
        [[ "${FAKE_DOCKER_PS_FAIL:-0}" != "1" ]] || exit 70
        [[ -f "$RUNNING_FILE" ]] && cat "$RUNNING_FILE"
        ;;
    stop)
        shift
        printf 'stop %s\n' "$*" >> "$DOCKER_LOG"
        : > "$RUNNING_FILE"
        ;;
    start)
        shift
        printf 'start %s\n' "$*" >> "$DOCKER_LOG"
        printf '%s\n' "$@" > "$RUNNING_FILE"
        ;;
    inspect)
        if [[ "${FAKE_DOCKER_INSPECT_FALSE:-0}" == "1" ]]; then
            echo false
        else
            echo true
        fi
        ;;
    *) exit 64 ;;
esac
EOF

cat > "$FAKE_BIN/rsync" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then
    echo 'info=progress2'
    exit 0
fi
printf 'copy\n' >> "$RSYNC_LOG"
if [[ -n "${FAKE_RSYNC_WAIT_DIR:-}" ]]; then
    touch "$FAKE_RSYNC_WAIT_DIR/ready"
    while [[ ! -f "$FAKE_RSYNC_WAIT_DIR/release" ]]; do
        sleep 0.05
    done
fi
if [[ "${FAKE_RSYNC_KILL:-0}" == "1" && ! -f "${RSYNC_LOG}.killed" ]]; then
    touch "${RSYNC_LOG}.killed"
    kill -KILL "$PPID"
    exit 137
fi
[[ "${FAKE_RSYNC_FAIL:-0}" != "1" ]] || exit 23
src="${@: -2:1}"
dest="${@: -1}"
cp -a "$src" "$dest"
EOF
chmod +x "$FAKE_BIN/docker" "$FAKE_BIN/rsync"

run_backup() {
    local output="$1"
    shift
    PATH="$FAKE_BIN:$PATH" ODS_DIR="$FAKE_ODS" \
        bash "$ODS_BACKUP" --output "$output" --type user-data "$@"
}

container_id=aaaaaaaaaaaa
printf '%s\n' "$container_id" > "$RUNNING_FILE"
run_backup "$TMP_ROOT/normal" >/dev/null
[[ $(sed -n '1p' "$DOCKER_LOG") == "stop $container_id" ]] || fail "containers were not stopped first"
[[ $(sed -n '2p' "$DOCKER_LOG") == "start $container_id" ]] || fail "exact container was not restarted"
manifest=$(find "$TMP_ROOT/normal" -name manifest.json -print -quit)
[[ $(jq -r '.consistency' "$manifest") == quiesced ]] || fail "manifest does not attest quiesced consistency"
[[ $(jq -r '.quiesced_container_count' "$manifest") == 1 ]] || fail "manifest container count is wrong"
pass "successful backup stops and restarts the exact running set"

: > "$DOCKER_LOG"
printf '%s\n' "$container_id" > "$RUNNING_FILE"
wait_dir="$TMP_ROOT/wait"
mkdir -p "$wait_dir"
FAKE_RSYNC_WAIT_DIR="$wait_dir" run_backup "$TMP_ROOT/concurrent-first" >/dev/null 2>&1 &
first_pid=$!
for _ in {1..100}; do
    [[ -f "$wait_dir/ready" ]] && break
    sleep 0.05
done
[[ -f "$wait_dir/ready" ]] || fail "first backup did not reach its copy boundary"
if run_backup "$TMP_ROOT/concurrent-second" >/dev/null 2>&1; then
    fail "concurrent backup bypassed the transaction lock"
fi
[[ $(grep -c '^stop ' "$DOCKER_LOG") -eq 1 ]] || fail "concurrent backup changed container state"
touch "$wait_dir/release"
wait "$first_pid" || fail "first concurrent backup failed"
pass "concurrent backup is rejected without a second lifecycle mutation"

: > "$DOCKER_LOG"
printf '%s\n' "$container_id" > "$RUNNING_FILE"
if FAKE_RSYNC_FAIL=1 run_backup "$TMP_ROOT/failure" >/dev/null 2>&1; then
    fail "copy failure was swallowed"
fi
grep -qx "start $container_id" "$DOCKER_LOG" || fail "copy failure did not restart the container"
[[ ! -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "successful recovery left a receipt"
pass "copy failure preserves the error and restores availability"

: > "$DOCKER_LOG"
printf '%s\n' "$container_id" > "$RUNNING_FILE"
if FAKE_DOCKER_INSPECT_FALSE=1 run_backup "$TMP_ROOT/restart-failure" >/dev/null 2>&1; then
    fail "failed restart verification was treated as success"
fi
[[ -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "restart failure discarded its recovery receipt"
run_backup "$TMP_ROOT/restart-recovery" >/dev/null
[[ ! -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "restart retry left a recovery receipt"
pass "restart verification failure is terminal and remains recoverable"

: > "$DOCKER_LOG"
: > "$RSYNC_LOG"
printf '%s\n' "$container_id" > "$RUNNING_FILE"
set +e
FAKE_RSYNC_KILL=1 run_backup "$TMP_ROOT/killed" >/dev/null 2>&1
killed_status=$?
set -e
[[ $killed_status -ne 0 ]] || fail "SIGKILL simulation unexpectedly succeeded"
[[ -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "SIGKILL did not leave a recovery receipt"
[[ ! -s "$RUNNING_FILE" ]] || fail "SIGKILL fixture did not leave the container stopped"

run_backup "$TMP_ROOT/recovered" >/dev/null
start_count=$(grep -c "^start $container_id$" "$DOCKER_LOG")
[[ $start_count -eq 2 ]] || fail "next backup did not recover then resume the container"
[[ ! -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "recovered transaction left a receipt"
pass "next public backup invocation recovers a process-death interruption"

: > "$DOCKER_LOG"
printf '%s\n' "$container_id" > "$RUNNING_FILE"
run_backup "$TMP_ROOT/live" --live >/dev/null
[[ ! -s "$DOCKER_LOG" ]] || fail "--live unexpectedly changed container state"
live_manifest=$(find "$TMP_ROOT/live" -name manifest.json -print -quit)
[[ $(jq -r '.consistency' "$live_manifest") == live-best-effort ]] \
    || fail "live manifest does not disclose weaker consistency"
pass "--live is explicit and records its weaker guarantee"

if FAKE_DOCKER_PS_FAIL=1 run_backup "$TMP_ROOT/daemon-failure" >/dev/null 2>&1; then
    fail "Docker enumeration failure was treated as a consistent backup"
fi
[[ ! -f "$FAKE_ODS/data/backup-quiesce.json" ]] || fail "enumeration failure left a false receipt"
[[ ! -d "$FAKE_ODS/data/.backup-quiesce.lock" ]] || fail "enumeration failure left a lock"
pass "Docker enumeration failure fails closed before copying data"
