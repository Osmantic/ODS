#!/usr/bin/env bash
# The macOS CLI must never signal a process only because its PID is in
# data/.llama-server.pid.
#
# A failed `launchctl bootstrap` leaves the previous PID in that file with no
# com.ods.llama-server job loaded. Once macOS reuses the PID for another program
# of the same user, `ods status` reported that program as llama-server,
# `ods start` refused to start the model, and `ods stop` / `ods restart` sent it
# SIGTERM and then SIGKILL. Only an install-owned llama-server may count as the
# native model, and only native-llama-service.sh stops processes.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"
TMP_DIR="$(mktemp -d)"
bystander=""
cleanup() {
    [[ -z "$bystander" ]] || kill "$bystander" 2>/dev/null || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

install="$TMP_DIR/install"
mkdir -p "$install/installers/macos/lib" "$install/data" "$install/bin" "$TMP_DIR/home" "$TMP_DIR/stubs"
# Model the already-verified manager boundary: no native producer is present.
cat > "$install/installers/macos/lib/native-llama-service.sh" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  status) printf '%s\n' '{"running":false,"identity":null}' ;;
  stop) rm -f "$4"; printf '%s\n' '{"stopped":false}' ;;
  *) exit 2 ;;
esac
STUB
# No com.ods.llama-server job is loaded, as after a failed bootstrap.
printf '#!/bin/sh\nexit 113\n' > "$TMP_DIR/stubs/launchctl"
chmod +x "$TMP_DIR/stubs/launchctl"

run_cli() {
    (
        export HOME="$TMP_DIR/home" INSTALL_DIR="$install" PATH="$TMP_DIR/stubs:$PATH"
        export ODS_LOG_FILE="$TMP_DIR/cli.log"
        # shellcheck source=/dev/null
        source "$CLI" help >/dev/null
        ODS_LOG_FILE="$TMP_DIR/cli.log"
        INSTALL_DIR="$install"
        LLAMA_SERVER_BIN="$install/bin/llama-server"
        LLAMA_SERVER_PID_FILE="$install/data/.llama-server.pid"
        # shellcheck disable=SC2329  # called by get_native_llama_status
        curl() { return 7; }
        "$@"
    )
}

report_status() {
    get_native_llama_status
    printf '%s %s\n' "$NATIVE_LLAMA_RUNNING" "$NATIVE_LLAMA_PID"
}

# An unrelated program of the same user now holds the PID recorded earlier.
sleep 300 &
bystander=$!
printf '%s\n' "$bystander" > "$install/data/.llama-server.pid"

status="$(run_cli report_status)"
[[ "$status" == "false 0" ]] \
    || fail "status treated an unrelated PID as llama-server: $status"
[[ "$(cat "$install/data/.llama-server.pid")" == "$bystander" ]] || fail "status changed the PID file"
pass "status follows the manager receipt and leaves the stale PID file untouched"

printf '%s\n' "$bystander" > "$install/data/.llama-server.pid"
run_cli stop_native_llama >/dev/null \
    || fail "stop_native_llama failed: $(cat "$TMP_DIR/cli.log" 2>/dev/null)"
sleep 0.5
kill -0 "$bystander" 2>/dev/null \
    || fail "stop_native_llama signalled an unrelated process from a stale PID file"
[[ ! -e "$install/data/.llama-server.pid" ]] \
    || fail "stop_native_llama kept the stale PID file"
pass "stop leaves an unrelated reused PID alone and clears the stale file"
