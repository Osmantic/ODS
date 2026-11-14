#!/usr/bin/env bash
# Exercise the shipped CLI dispatcher without touching an installed backup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
INSTALL="$WORK/install with spaces"
CALLER="$WORK/caller with spaces"
mkdir -p "$INSTALL/lib" "$CALLER"
cp "$ROOT/ods-cli" "$INSTALL/ods-cli"
cp "$ROOT/lib/service-registry.sh" "$INSTALL/lib/"
: > "$INSTALL/docker-compose.base.yml"
: > "$INSTALL/.env"

fail() { echo "[FAIL] $*" >&2; exit 1; }
for helper in backup restore; do
    cat > "$INSTALL/ods-$helper.sh" <<'HELPER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\0' "${0##*/}" "$PWD" "$@" > "$CAPTURE"
printf 'child stdout\n'
printf 'child stderr\n' >&2
exit "${CHILD_STATUS:-0}"
HELPER
    chmod +x "$INSTALL/ods-$helper.sh"
done

run_cli() {
    local expected_status="$1"
    shift
    rm -f "$WORK/capture"
    local status=0
    (cd "$CALLER" && env -u ODS_HOME -u ODS_SCRIPT_HINT -u ODS_INSTALL_DIR \
        INSTALL_DIR="$INSTALL" CAPTURE="$WORK/capture" \
        CHILD_STATUS="${CHILD_STATUS:-0}" bash "$INSTALL/ods-cli" "$@") \
        > "$WORK/stdout" 2> "$WORK/stderr" || status=$?
    [[ "$status" == "$expected_status" ]] \
        || fail "$* exited $status instead of $expected_status"
}

assert_call() {
    printf '%s\0' "$@" > "$WORK/expected"
    cmp -s "$WORK/expected" "$WORK/capture" || fail "wrong helper, working directory or argument boundaries"
    grep -Fxq 'child stdout' "$WORK/stdout" || fail 'child stdout was lost'
    grep -Fxq 'child stderr' "$WORK/stderr" || fail 'child stderr was lost'
}

run_cli 0 backup
assert_call ods-backup.sh "$INSTALL"
run_cli 0 backup --type config --description 'before upgrade *' --output 'external backups'
assert_call ods-backup.sh "$INSTALL" --type config --description 'before upgrade *' --output "$CALLER/external backups"
run_cli 0 backup -o "$WORK/absolute backups" --compress
assert_call ods-backup.sh "$INSTALL" -o "$WORK/absolute backups" --compress
run_cli 0 backup verify 'backup-123-20261025-120000.tar.gz'
assert_call ods-backup.sh "$INSTALL" verify 'backup-123-20261025-120000.tar.gz'
run_cli 0 restore --dry-run --force 'backup-123-20261025-120000'
assert_call ods-restore.sh "$INSTALL" --dry-run --force 'backup-123-20261025-120000'
echo '[PASS] backup and restore dispatch preserve arguments, paths and output'

for command in backup restore; do
    CHILD_STATUS=23 run_cli 23 "$command" --help
    assert_call "ods-$command.sh" "$INSTALL" --help
done
CHILD_STATUS=29 run_cli 29 backup verify backup-123-20261025-120000
assert_call ods-backup.sh "$INSTALL" verify backup-123-20261025-120000
echo '[PASS] backup, verify and restore propagate child failure status'

run_cli 1 backup verify
[[ ! -e "$WORK/capture" ]] || fail 'verify without an ID invoked the backup helper'
grep -Fq 'Usage: ods backup verify' "$WORK/stderr" || fail 'missing verify ID has no usage guidance'
for command in backup restore; do
    mv "$INSTALL/ods-$command.sh" "$INSTALL/ods-$command.sh.unavailable"
    run_cli 1 "$command"
    [[ ! -e "$WORK/capture" ]] || fail 'unavailable helper was invoked'
    grep -Fq "ods-$command.sh not found or not executable" "$WORK/stderr" \
        || fail 'missing helper has no actionable diagnostic'
done
echo '[PASS] invalid verify and unavailable helpers refuse before dispatch'
