#!/usr/bin/env bash
# A forced reinstall can race launchd's asynchronous bootout of the old bridge.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/home" "$tmp/install/bin"
: > "$tmp/install/bin/ods-macos-llm-bridge.py"

cat > "$tmp/bin/launchctl" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == bootstrap ]] || exit 0
attempts=0
[[ ! -f "$BRIDGE_TEST_COUNT" ]] || attempts="$(<"$BRIDGE_TEST_COUNT")"
attempts=$((attempts + 1))
printf '%s\n' "$attempts" > "$BRIDGE_TEST_COUNT"
case "$BRIDGE_TEST_MODE" in
    transient) [[ "$attempts" -gt 1 ]] && exit 0; exit 5 ;;
    persistent) exit 5 ;;
    other) exit 4 ;;
esac
exit 1
STUB
cat > "$tmp/bin/bridge-python" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$tmp/bin/launchctl" "$tmp/bin/bridge-python"

export HOME="$tmp/home" PATH="$tmp/bin:$PATH"
export MACOS_BRIDGE_PYTHON="$tmp/bin/bridge-python"
export BRIDGE_TEST_COUNT="$tmp/attempts" BRIDGE_TEST_MODE
ai_err() { printf 'ERROR %s\n' "$*" >&2; }
ai_ok() { :; }
sleep() { :; }
source "$root/installers/macos/lib/bridge-manager.sh"

run_bridge() {
    macos_configure_port_bridge true com.ods.test "$tmp/bridge.plist" "$tmp/bridge.log" \
        "test bridge" 192.168.64.1 8080 8080 192.168.64.2 "$tmp/install"
}

BRIDGE_TEST_MODE=transient
run_bridge
[[ "$(<"$BRIDGE_TEST_COUNT")" == 2 ]]

rm -f "$BRIDGE_TEST_COUNT"
BRIDGE_TEST_MODE=persistent
if run_bridge; then
    echo 'persistent launchctl rc=5 was accepted' >&2
    exit 1
fi
[[ "$(<"$BRIDGE_TEST_COUNT")" == 6 ]]

rm -f "$BRIDGE_TEST_COUNT"
BRIDGE_TEST_MODE=other
if run_bridge; then
    echo 'non-retryable launchctl error was accepted' >&2
    exit 1
fi
[[ "$(<"$BRIDGE_TEST_COUNT")" == 1 ]]

echo 'macOS bridge bootstrap retry contract passed'
