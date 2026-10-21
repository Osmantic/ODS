#!/usr/bin/env bash
# health-check.sh must not report CRITICAL for a healthy base stack.
#
# The core loop probed every core manifest over host HTTP, including services
# with no host port (Pixel host agent, Pixel edge, remote-provider sidecars),
# the container-health model router, and Pixel fragments that ship disabled.
# Each became a failed core check, so every healthy install exited 2.
#
# Run from repo root:  bash ods/tests/test-health-check-core-services.sh
# Or from ods:         bash tests/test-health-check-core-services.sh
set -uo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        # The probe must expand in the candidate shell, not this one.
        # shellcheck disable=SC2016
        if [ -x "$_modern_bash" ] && [ "$("$_modern_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] health-check.sh requires Bash 4+ (service registry); this host only has Bash ${BASH_VERSION}"
    echo "Result: 0 passed, 0 failed, 1 skipped"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HC="$ROOT_DIR/scripts/health-check.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
[[ -f "$HC" ]] || { echo "health-check.sh not found"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/install" "$TMP/bin"
: > "$TMP/install/.env"

# docker stub: the base stack is running; the router's own healthcheck reports
# ROUTER_HEALTH; RELAY_STATE, when set, gives the Pixel relay a container.
cat > "$TMP/bin/docker" <<'STUB'
#!/bin/sh
name=""
for arg in "$@"; do name="$arg"; done
case "$name" in
    ods-llama-server|ods-webui|ods-dashboard|ods-dashboard-api) state=running ;;
    ods-model-router) state=running; health="$ROUTER_HEALTH" ;;
    ods-pixel-model-relay) state="${RELAY_STATE:-}" ;;
    *) state="" ;;
esac
if [ -z "$state" ]; then
    echo "Error: No such object: $name" >&2
    exit 1
fi
case "$*" in
    *Health*) echo "${health:-}" ;;
    *) echo "$state" ;;
esac
STUB
# curl stub: inference and the base stack's host ports answer.
cat > "$TMP/bin/curl" <<'STUB'
#!/bin/sh
url=""
for arg in "$@"; do
    case "$arg" in http://*) url="$arg" ;; esac
done
case "$url" in
    */v1/completions) echo '{"choices":[{"text":"Hi"}]}' ;;
    http://127.0.0.1:3000/*|http://127.0.0.1:3001/*|http://127.0.0.1:3002/*) echo ok ;;
    *) exit 7 ;;
esac
STUB
chmod +x "$TMP/bin/docker" "$TMP/bin/curl"

run_hc() {
    env ROUTER_HEALTH="$1" RELAY_STATE="$2" LLM_PORT=11434 INSTALL_DIR="$TMP/install" \
        TIMEOUT=1 PATH="$TMP/bin:$PATH" "$BASH" "$HC" --json > "$TMP/out.json" 2> "$TMP/err.txt"
}

# Print "<status> <service>=<result> ..." for the requested service ids.
summary() {
    python3 - "$TMP/out.json" "$@" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
services = doc["services"]
print(doc["status"], " ".join(f"{sid}={services.get(sid, 'absent')}" for sid in sys.argv[2:]))
PY
}

echo "Test 1: a healthy base stack is not CRITICAL"
run_hc healthy ""
got="$(summary dashboard dashboard-api open-webui model-router)"
if [[ "$got" != critical* && "$got" == *" dashboard=ok dashboard-api=ok open-webui=ok model-router=ok" ]]; then
    pass "base stack healthy: $got"
else
    fail "healthy base stack reported: $got"
fi
got="$(summary pixel-agent pixel-edge pixel-model-relay remote-provider-egress remote-provider-ssh-tunnel)"
if [[ "$got" == *" pixel-agent=absent pixel-edge=absent pixel-model-relay=absent remote-provider-egress=absent remote-provider-ssh-tunnel=absent" ]]; then
    pass "services without a host probe or install are not reported failed"
else
    fail "unprobeable core services still reported: $got"
fi

echo "Test 2: an unhealthy model router is still CRITICAL"
run_hc unhealthy ""
got="$(summary model-router)"
if [[ "$got" == "critical model-router=fail" ]]; then
    pass "router container health is enforced"
else
    fail "unhealthy router reported: $got"
fi

echo "Test 3: an installed but stopped Pixel relay is still CRITICAL"
run_hc healthy exited
got="$(summary pixel-model-relay)"
if [[ "$got" == "critical pixel-model-relay=fail" ]]; then
    pass "a relay container that exists is still checked"
else
    fail "stopped relay reported: $got"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
