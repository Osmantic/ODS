#!/usr/bin/env bash
# Regression: every spawn site that launches scripts/bootstrap-upgrade.sh as a
# long-lived nohup background daemon MUST close inherited non-stdio file
# descriptors before exec. Otherwise the daemon holds any flock its caller
# opened (e.g. FD 9 from the fleet harness, or common FD 200 wrappers) for the
# full lifetime of the background model download.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

assert_fd_close_spawn() {
    local target="$1"
    local label="$2"

    [[ -f "$target" ]] || fail "missing $target"

    grep -qE '(/proc/\$\{BASHPID:-\$\}/fd|/dev/fd)' "$target" \
        || fail "$label: helper must enumerate inherited FDs via /proc/\${BASHPID:-\$\$}/fd or /dev/fd"
    grep -q 'fd_name <= 254' "$target" \
        || fail "$label: helper must include a numeric fallback that covers common FD 200 flock wrappers"

    awk '
        /Start the long-lived downloader from a child shell/ { in_block=1; close_seen=0; exec_seen=0 }
        in_block && /close_inherited_fds_for_daemon/ { close_seen=1 }
        in_block && (/exec[[:space:]]+nohup[[:space:]]+bash/ || /_macos_launch_detached_bootstrap_upgrade/) && close_seen { exec_seen=1 }
        in_block && /^[[:space:]]*\)[[:space:]]*&/ { exit(exec_seen ? 0 : 1) }
        END { if (!exec_seen) exit 1 }
    ' "$target" || fail "$label: bootstrap-upgrade.sh must be spawned from a child shell after closing inherited FDs"

    pass "$label: bootstrap-upgrade.sh spawn closes inherited non-stdio FDs before exec"
}

assert_runtime_lock_release() {
    local fd="$1"
    local lock_file pid_file log_file
    lock_file="$(mktemp "${TMPDIR:-/tmp}/ods-fd-${fd}.XXXXXX.lock")"
    pid_file="$(mktemp "${TMPDIR:-/tmp}/ods-fd-${fd}.XXXXXX.pid")"
    log_file="$(mktemp "${TMPDIR:-/tmp}/ods-fd-${fd}.XXXXXX.log")"

    _test_close_inherited_fds_for_daemon() {
        local fd_path fd_dir fd_name
        for fd_dir in "/proc/${BASHPID:-$$}/fd" "/dev/fd"; do
            [[ -d "$fd_dir" ]] || continue
            for fd_path in "$fd_dir"/*; do
                fd_name="${fd_path##*/}"
                [[ "$fd_name" =~ ^[0-9]+$ ]] || continue
                (( fd_name <= 2 || fd_name == 255 )) && continue
                eval "exec ${fd_name}>&-" 2>/dev/null || true
            done
            return 0
        done
        for ((fd_name = 3; fd_name <= 254; fd_name++)); do
            eval "exec ${fd_name}>&-" 2>/dev/null || true
        done
    }

    (
        eval "exec ${fd}>\"$lock_file\""
        flock -x "$fd"
        (
            _test_close_inherited_fds_for_daemon
            exec nohup bash -c 'sleep 10' >"$log_file" 2>&1
        ) &
        echo $! > "$pid_file"
    )

    sleep 0.2
    if flock -n "$lock_file" -c true; then
        pass "runtime check: inherited FD $fd lock is released after daemon spawn"
    else
        [[ -s "$pid_file" ]] && kill "$(cat "$pid_file")" >/dev/null 2>&1 || true
        fail "runtime check: inherited FD $fd lock is still held by the daemon"
    fi

    [[ -s "$pid_file" ]] && kill "$(cat "$pid_file")" >/dev/null 2>&1 || true
    rm -f "$lock_file" "$pid_file" "$log_file"
}

assert_fd_close_spawn "$ROOT_DIR/installers/phases/11-services.sh"   "linux/wsl phase 11"
assert_fd_close_spawn "$ROOT_DIR/installers/macos/install-macos.sh" "macos installer"

# flock(1) is util-linux and does not exist on macOS. The static spawn-site
# checks above cover the product contract on every platform; the runtime
# lock-release simulation only runs where flock is available.
if command -v flock >/dev/null 2>&1; then
    assert_runtime_lock_release 9
    assert_runtime_lock_release 200
else
    echo "[SKIP] runtime lock-release checks: flock(1) not available on this platform"
fi

echo "[OK] all bootstrap-upgrade spawn sites close inherited non-stdio FDs"
