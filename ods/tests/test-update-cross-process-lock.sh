#!/usr/bin/env bash
# `ods-update.sh update` and `rollback` stop services, rewrite configuration and
# move the git checkout. The host agent serializes its own update runs with an
# in-process lock only, so a dashboard update and a shell `ods-update.sh
# update` could interleave: one run's `compose down` breaks the other's health
# wait, which then rolls back mid-way. Mutating commands must hold one
# cross-process lock and fail fast when another run holds it.
#
# Runs the real updater against a throwaway git checkout. Docker, Python and
# curl are stubs; the Docker stub holds `compose down` open so two runs overlap.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="${ODS_UPDATE_UNDER_TEST:-$ROOT_DIR/ods-update.sh}"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_DIR="$TMP_DIR/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
case " $* " in
    *" down "*)
        if mkdir "$CRITICAL_MARK" 2>/dev/null; then
            echo "enter $PPID" >> "$DOCKER_LOG"
            sleep "${DOWN_SECONDS:-0}"
            rmdir "$CRITICAL_MARK"
        else
            echo "overlap $PPID" >> "$DOCKER_LOG"
        fi
        ;;
    *" up -d "*) [[ -f "$UP_FAILS" ]] && exit 1 ;;
    *" config --format json "*) echo '{}' ;;
    *" ps --services "*) echo dashboard-api ;;
    *" ps --format json "*) echo '{"State":"running"}' ;;
esac
exit 0
SH
cp "$BIN_DIR/docker" "$BIN_DIR/docker-compose"
cat > "$BIN_DIR/python3" <<'SH'
#!/usr/bin/env bash
[[ "${2:-}" == compose ]] && cat >/dev/null
exit 0
SH
printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN_DIR/curl"
chmod +x "$BIN_DIR"/*

git_quiet() { git -c user.name=ods-test -c user.email=ods-test@example.invalid "$@" >/dev/null 2>&1; }

INSTALL="$TMP_DIR/install"
WORK="$TMP_DIR/work"
git_quiet init -q --bare "$TMP_DIR/origin.git"
git_quiet init -q "$WORK"
mkdir -p "$WORK/scripts"
cp "$UPDATE_SCRIPT" "$WORK/ods-update.sh"
printf 'print("stub")\n' > "$WORK/scripts/source-update-preflight.py"
printf 'services: {dashboard-api: {image: example/dashboard-api:test}}\n' > "$WORK/docker-compose.base.yml"
printf 'data/\n.compose-flags\n.env\n.version\n' > "$WORK/.gitignore"
git_quiet -C "$WORK" add -A
git_quiet -C "$WORK" commit -q -m initial
git_quiet -C "$WORK" branch -M main
git_quiet -C "$WORK" remote add origin "$TMP_DIR/origin.git"
git_quiet -C "$WORK" push -q origin main
git_quiet clone -q -c core.autocrlf=false -b main "$TMP_DIR/origin.git" "$INSTALL"
printf '%s\n' '-f docker-compose.base.yml' > "$INSTALL/.compose-flags"
printf 'GPU_BACKEND=cpu\n' > "$INSTALL/.env"
mkdir -p "$TMP_DIR/home"

run_cmd() {
    local out="$1"; shift
    (cd "$INSTALL" && HOME="$TMP_DIR/home" PATH="$BIN_DIR:$PATH" \
        DOCKER_LOG="$TMP_DIR/docker.log" CRITICAL_MARK="$TMP_DIR/critical" \
        UP_FAILS="$TMP_DIR/up-fails" HEALTH_TIMEOUT=5 bash ./ods-update.sh "$@") > "$out" 2>&1
}

check_mode() {
    local mode="$1"
    rm -f "$TMP_DIR/docker.log"
    # ── A second run while the first is inside the critical section ──────────
    DOWN_SECONDS=6 run_cmd "$TMP_DIR/first.out" update &
    local first=$!
    local waited=0
    until [[ -d "$TMP_DIR/critical" ]]; do
        sleep 0.2; waited=$((waited + 1))
        (( waited < 150 )) || { cat "$TMP_DIR/first.out"; fail "[$mode] first update never reached compose down"; }
    done
    local second_status=0
    DOWN_SECONDS=0 run_cmd "$TMP_DIR/second.out" update || second_status=$?
    local third_status=0
    DOWN_SECONDS=0 run_cmd "$TMP_DIR/third.out" rollback || third_status=$?
    local first_status=0
    wait "$first" || first_status=$?

    if grep -q '^overlap' "$TMP_DIR/docker.log"; then
        cat "$TMP_DIR/docker.log"; cat "$TMP_DIR/second.out"
        fail "[$mode] two update runs were inside compose down at the same time"
    fi
    (( second_status != 0 )) || { cat "$TMP_DIR/second.out"; fail "[$mode] concurrent update exited 0"; }
    grep -Fq 'another update is in progress' "$TMP_DIR/second.out" \
        || { cat "$TMP_DIR/second.out"; fail "[$mode] concurrent update did not explain the contention"; }
    (( third_status != 0 )) && grep -Fq 'another update is in progress' "$TMP_DIR/third.out" \
        || { cat "$TMP_DIR/third.out"; fail "[$mode] concurrent rollback was not refused"; }
    (( first_status == 0 )) || { cat "$TMP_DIR/first.out"; fail "[$mode] lock holder failed"; }
    pass "[$mode] a concurrent update or rollback fails fast while an update runs"

    # ── Released after a normal completion ───────────────────────────────────
    run_cmd "$TMP_DIR/after-ok.out" update \
        || { cat "$TMP_DIR/after-ok.out"; fail "[$mode] lock was not released after a successful update"; }
    pass "[$mode] lock is released after a successful update"

    # ── Released after a failed update (restart fails, rollback runs) ────────
    touch "$TMP_DIR/up-fails"
    if run_cmd "$TMP_DIR/failed.out" update; then
        fail "[$mode] fixture update should have failed"
    fi
    rm -f "$TMP_DIR/up-fails"
    grep -Fq 'another update is in progress' "$TMP_DIR/failed.out" \
        && { cat "$TMP_DIR/failed.out"; fail "[$mode] update rejected by its own lock"; }
    run_cmd "$TMP_DIR/after-fail.out" update \
        || { cat "$TMP_DIR/after-fail.out"; fail "[$mode] lock was not released after a failed update"; }
    pass "[$mode] lock is released after a failed update"
}

# The macOS host agent runs ods-update.sh with /bin/bash 3.2, which has no
# {var}> fd allocation (bash 4.1+); with flock on PATH that aborted rc 127.
if grep -En 'exec[[:space:]]+\{[A-Za-z_][A-Za-z0-9_]*\}[<>]' "$ROOT_DIR/ods-update.sh"; then
    fail "ods-update.sh uses {var}> fd allocation, which bash 3.2 (macOS /bin/bash) lacks"
fi
pass "lock fd redirection is bash 3.2 compatible"

if command -v flock >/dev/null 2>&1; then
    check_mode flock
fi
export ODS_UPDATE_LOCK_NO_FLOCK=1
check_mode mkdir

# ── A lock left by a dead process is reclaimed (mkdir fallback) ──────────────
mkdir -p "$INSTALL/data/.ods-update.lock.d"
bash -c 'exit 0' & dead=$!; wait "$dead"
echo "$dead" > "$INSTALL/data/.ods-update.lock.d/pid"
run_cmd "$TMP_DIR/stale.out" update \
    || { cat "$TMP_DIR/stale.out"; fail "stale mkdir lock from a dead process was not reclaimed"; }
pass "a stale lock from a dead process is reclaimed"

echo "All update cross-process lock tests passed."
