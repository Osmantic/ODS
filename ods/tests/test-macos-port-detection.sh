#!/usr/bin/env bash
# Regression: macOS port checks must see listeners hidden from unprivileged
# lsof (for example a root-owned Lemonade daemon on Whisper's port 9000).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DETECTION="$ROOT_DIR/installers/macos/lib/detection.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

function_block() {
    local function_name="$1"
    awk -v signature="^${function_name}[(][)]" '
        $0 ~ signature { capture=1 }
        capture { print }
        capture && /^}/ { exit }
    ' "$DETECTION"
}

body="$(function_block check_port_conflict)"
[[ -n "$body" ]] || fail "could not extract check_port_conflict"
eval "$body"

MOCK_LSOF_VISIBLE=false
MOCK_NC_OPEN=false
MOCK_NC_ARGS=""

lsof() {
    if $MOCK_LSOF_VISIBLE; then
        if [[ " $* " == *" -t "* ]]; then
            printf '4242\n'
        fi
        return 0
    fi
    return 1
}

nc() {
    MOCK_NC_ARGS="$*"
    $MOCK_NC_OPEN
}

ps() {
    printf 'visible-listener\n'
}

MOCK_LSOF_VISIBLE=true
MOCK_NC_OPEN=false
check_port_conflict 8080 || fail "lsof-visible listener was missed"
[[ "$PORT_CONFLICT" == true ]] || fail "visible listener did not set conflict"
[[ "$PORT_CONFLICT_PID" == 4242 ]] || fail "visible listener PID was lost"
[[ "$PORT_CONFLICT_PROC" == visible-listener ]] \
    || fail "visible listener process name was lost"
pass "lsof-visible listener retains diagnostic identity"

MOCK_LSOF_VISIBLE=false
MOCK_NC_OPEN=true
MOCK_NC_ARGS=""
check_port_conflict 9000 "existing listener" || fail "root-hidden loopback listener was missed"
[[ "$PORT_CONFLICT" == true ]] || fail "hidden listener did not set conflict"
[[ -z "$PORT_CONFLICT_PID" ]] || fail "hidden listener invented a PID"
[[ "$PORT_CONFLICT_PROC" == "existing listener" ]] \
    || fail "hidden listener did not retain its caller label"
[[ "$MOCK_NC_ARGS" == "-z -w 1 127.0.0.1 9000" ]] \
    || fail "loopback fallback was not bounded and scoped: $MOCK_NC_ARGS"
pass "bounded loopback handshake detects a root-hidden listener"

MOCK_LSOF_VISIBLE=false
MOCK_NC_OPEN=false
PORT_CONFLICT=true
PORT_CONFLICT_PID=stale
PORT_CONFLICT_PROC=stale
if check_port_conflict 9100; then
    fail "unused port was reported occupied"
fi
[[ "$PORT_CONFLICT" == false ]] || fail "unused port did not clear conflict"
[[ -z "$PORT_CONFLICT_PID" && -z "$PORT_CONFLICT_PROC" ]] \
    || fail "unused port retained stale diagnostic identity"
pass "unused port remains available and clears stale identity"

grep -qF 'export WHISPER_PORT=9100' "$ROOT_DIR/installers/macos/install-macos.sh" \
    || fail "macOS installer no longer reassigns Whisper conflicts"
grep -qF 'Port 9000 in use by ${PORT_CONFLICT_PROC}' "$ROOT_DIR/installers/macos/install-macos.sh" \
    || fail "Whisper reassignment does not report the detected listener"
pass "macOS Whisper fallback remains wired to conflict detection"

echo "[OK] macOS hidden-listener detection contract holds"
