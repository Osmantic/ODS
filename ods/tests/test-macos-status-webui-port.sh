#!/usr/bin/env bash
# `ods status` on macOS must probe Open WebUI on the port Compose publishes it
# on (${WEBUI_PORT:-3000} in docker-compose.base.yml). It used to probe
# 127.0.0.1:3000 regardless, so an owner who moved Open WebUI with WEBUI_PORT,
# as TROUBLESHOOTING.md suggests for a port conflict, was told a healthy Open
# WebUI was not responding.
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
server=""
cleanup() {
    [[ -z "$server" ]] || kill "$server" 2>/dev/null || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

free_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

mkdir -p "$TMP_DIR/install/data" "$TMP_DIR/home" "$TMP_DIR/stubs"
cat > "$TMP_DIR/stubs/docker" <<'STUB'
#!/bin/sh
case "$*" in *"config --services"*) echo open-webui ;; esac
exit 0
STUB
chmod +x "$TMP_DIR/stubs/docker"

# Prints the Open WebUI line of `ods status` for the given .env content.
webui_status() {
    printf '%s' "$1" > "$TMP_DIR/install/.env"
    (
        export HOME="$TMP_DIR/home" INSTALL_DIR="$TMP_DIR/install" PATH="$TMP_DIR/stubs:$PATH"
        export ODS_LOG_FILE="$TMP_DIR/cli.log"
        # shellcheck source=/dev/null
        source "$CLI" help >/dev/null
        ODS_LOG_FILE="$TMP_DIR/cli.log"
        INSTALL_DIR="$TMP_DIR/install"
        # shellcheck disable=SC2329  # stand-ins for the host, called by cmd_status
        test_install() { :; }
        # shellcheck disable=SC2329
        get_compose_flags() { echo "-f docker-compose.base.yml"; }
        # shellcheck disable=SC2329
        get_apple_silicon_info() { APPLE_CHIP=M2; }
        # shellcheck disable=SC2329
        get_system_ram_gb() { SYSTEM_RAM_GB=16; }
        # shellcheck disable=SC2329
        get_native_llama_status() { NATIVE_LLAMA_RUNNING=false; }
        cmd_status 2>&1
    ) | sed 's/\x1b\[[0-9;]*m//g' | grep "Open WebUI" || true
}

# Open WebUI answers on a port other than 3000, as after a WEBUI_PORT change.
port="$(free_port)"
python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 &
server=$!
for _ in $(seq 1 50); do
    curl -s -o /dev/null "http://127.0.0.1:$port/" && break
    sleep 0.1
done

line="$(webui_status "ODS_MODE=local
WEBUI_PORT=$port
")"
[[ "$line" == *healthy* ]] || fail "status did not find Open WebUI on WEBUI_PORT=$port: $line"
pass "status probes Open WebUI on WEBUI_PORT"

line="$(webui_status "ODS_MODE=local
WEBUI_PORT=\"$port\" # moved off 3000
")"
[[ "$line" == *healthy* ]] || fail "status did not decode a quoted WEBUI_PORT with a comment: $line"
pass "status decodes WEBUI_PORT as Compose does"

if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:3000/"; then
    echo "[SKIP] something already answers on 127.0.0.1:3000; fallback case not checked"
    exit 0
fi
line="$(webui_status "ODS_MODE=local
WEBUI_PORT=not-a-port
")"
[[ "$line" == *"not responding"* ]] \
    || fail "an invalid WEBUI_PORT should fall back to 3000, where nothing answers: $line"
pass "an invalid WEBUI_PORT falls back to the Compose default"
