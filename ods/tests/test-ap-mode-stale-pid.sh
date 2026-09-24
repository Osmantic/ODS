#!/usr/bin/env bash
# Regression contract: `ap-mode.sh down` must not signal a recycled PID.
#
# cmd_down runs as root and previously did `kill "$(cat pidfile)"` with no
# identity check — a stale pidfile (daemon crashed, PID recycled by an
# unrelated process) meant killing an innocent process. The fix verifies
# /proc/<pid>/comm and the conf path in cmdline before signalling, the
# same identity the pkill fallback already required.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/ods/scripts/ap-mode.sh"

fail=0
check() {
    if eval "$2"; then
        echo "PASS: $1"
    else
        echo "FAIL: $1"
        fail=1
    fi
}

# ── Static contract ─────────────────────────────────────────────────────────

check "no bare kill from pidfile contents remains" \
    '! grep -n "kill \"\$(cat" "$SCRIPT"'

check "both daemons go through kill_pidfile" \
    'grep -q "kill_pidfile \"\${HOSTAPD_PID}\" hostapd" "$SCRIPT" &&
     grep -q "kill_pidfile \"\${DNSMASQ_PID}\" dnsmasq" "$SCRIPT"'

check "identity verified via /proc comm and conf path" \
    'grep -q "/proc/\$pid/comm" "$SCRIPT" && grep -q "/proc/\$pid/cmdline" "$SCRIPT"'

# ── Behavioral: run the real cmd_down against live processes ────────────────

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; jobs -p | xargs -r kill 2>/dev/null || true' EXIT

# Stub every external command cmd_down touches. iptables must FAIL its -C
# existence check or remove_iptables_rules' `while iptables -C ... -D` loop
# never terminates.
STUBS="$TMP/bin"
mkdir -p "$STUBS"
for cmd in pkill ip nmcli; do
    printf '#!/usr/bin/env bash\necho "%s $*" >> "%s/stub-calls.log"\nexit 0\n' "$cmd" "$TMP" > "$STUBS/$cmd"
    chmod +x "$STUBS/$cmd"
done
printf '#!/usr/bin/env bash\necho "iptables $*" >> "%s/stub-calls.log"\n[[ "$*" == *" -C "* ]] && exit 1 || exit 0\n' "$TMP" > "$STUBS/iptables"
chmod +x "$STUBS/iptables"

# A binary named like the daemon so /proc/<pid>/comm matches.
cp "$(command -v tail)" "$STUBS/hostapd"
cp "$(command -v tail)" "$STUBS/dnsmasq"

run_down() {
    env -i PATH="$STUBS:/usr/bin:/bin" HOME="$TMP" bash -c '
        set -euo pipefail
        export ODS_AP_CONF_DIR="'"$TMP"'/conf" ODS_AP_RUN_DIR="'"$TMP"'/run"
        mkdir -p "$ODS_AP_CONF_DIR" "$ODS_AP_RUN_DIR"
        source "'"$SCRIPT"'"
        require_linux() { :; }
        require_root() { :; }
        cmd_down
    '
}

# Scenario A: pidfile names a recycled PID held by an unrelated process.
sleep 60 &
SLEEP_PID=$!
mkdir -p "$TMP/run" "$TMP/conf"
echo "$SLEEP_PID" > "$TMP/run/hostapd.pid"
run_down > "$TMP/out-a.log" 2>&1
check "recycled PID is NOT killed (sleep survives cmd_down)" \
    'kill -0 "$SLEEP_PID" 2>/dev/null'
check "stale pidfile is still removed" \
    '! [[ -f "$TMP/run/hostapd.pid" ]]'
check "skip is logged for the operator" \
    'grep -q "skipping kill" "$TMP/out-a.log"'
kill "$SLEEP_PID" 2>/dev/null || true
wait "$SLEEP_PID" 2>/dev/null || true

# Scenario B: pidfile names a live process that IS the daemon.
touch "$TMP/run/hostapd.conf"
"$STUBS/hostapd" -f "$TMP/run/hostapd.conf" &
AP_PID=$!
echo "$AP_PID" > "$TMP/run/hostapd.pid"
run_down > "$TMP/out-b.log" 2>&1
check "matching daemon IS killed" '! kill -0 "$AP_PID" 2>/dev/null'

# Scenario C: pidfile names a dead PID — must not error, file removed.
echo "999999" > "$TMP/run/hostapd.pid"
run_down > "$TMP/out-c.log" 2>&1
check "dead pidfile handled without error" '! [[ -f "$TMP/run/hostapd.pid" ]]'

# Scenario D: malformed pidfile contents — must not error, file removed.
echo "not-a-pid" > "$TMP/run/dnsmasq.pid"
run_down > "$TMP/out-d.log" 2>&1
check "malformed pidfile handled without error" '! [[ -f "$TMP/run/dnsmasq.pid" ]]'

if [[ $fail -eq 0 ]]; then
    echo "PASS: ap-mode down verifies pid identity before signalling"
else
    exit 1
fi
