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
MOCK_NC_MODE=closed

lsof() {
    if $MOCK_LSOF_VISIBLE; then
        if [[ " $* " == *" -t "* ]]; then
            printf '4242\n'
        fi
        return 0
    fi
    return 1
}

STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
export MOCK_NC_ARGS_FILE="$STUB_DIR/args" MOCK_NC_PID_FILE="$STUB_DIR/pid"
export MOCK_NC_MODE MOCK_NC_OPEN
cat >"$STUB_DIR/nc" <<'STUB'
#!/usr/bin/env python3
import os
import signal
import sys
from pathlib import Path

Path(os.environ['MOCK_NC_ARGS_FILE']).write_text(' '.join(sys.argv[1:]))
if os.environ['MOCK_NC_MODE'] == 'stall':
    Path(os.environ['MOCK_NC_PID_FILE']).write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.pause()
sys.exit(0 if os.environ['MOCK_NC_OPEN'] == 'true' else 1)
STUB
chmod +x "$STUB_DIR/nc"
export PATH="$STUB_DIR:$PATH"

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
MOCK_NC_MODE=closed
check_port_conflict 9000 "existing listener" || fail "root-hidden loopback listener was missed"
[[ "$PORT_CONFLICT" == true ]] || fail "hidden listener did not set conflict"
[[ -z "$PORT_CONFLICT_PID" ]] || fail "hidden listener invented a PID"
[[ "$PORT_CONFLICT_PROC" == "existing listener" ]] \
    || fail "hidden listener did not retain its caller label"
[[ "$(cat "$MOCK_NC_ARGS_FILE")" == "-z -w 1 127.0.0.1 9000" ]] \
    || fail "loopback fallback was not bounded and scoped: $(cat "$MOCK_NC_ARGS_FILE")"
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

MOCK_NC_MODE=stall
stall_start=$SECONDS
if check_port_conflict 9200; then
    fail "stalled probe was reported occupied"
fi
stall_elapsed=$(( SECONDS - stall_start ))
(( stall_elapsed < 8 )) || fail "stalled probe blocked for ${stall_elapsed}s"
[[ "$PORT_CONFLICT" == false ]] || fail "stalled probe retained a conflict"
[[ -z "$PORT_CONFLICT_PID" && -z "$PORT_CONFLICT_PROC" ]] || fail "stalled probe retained identity"
[[ -s "$MOCK_NC_PID_FILE" ]] || fail "stalled probe never started"
if kill -0 "$(cat "$MOCK_NC_PID_FILE")" 2>/dev/null; then
    fail "stalled probe child survived its timeout"
fi
MOCK_NC_MODE=closed
pass "stalled probe returns within ${stall_elapsed}s and its child is reaped"

grep -qF 'export WHISPER_PORT=9100' "$ROOT_DIR/installers/macos/install-macos.sh" \
    || fail "macOS installer no longer reassigns Whisper conflicts"
grep -qF 'Port 9000 in use by ${PORT_CONFLICT_PROC}' "$ROOT_DIR/installers/macos/install-macos.sh" \
    || fail "Whisper reassignment does not report the detected listener"
pass "macOS Whisper fallback remains wired to conflict detection"

echo "[OK] macOS hidden-listener detection contract holds"
