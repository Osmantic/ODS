#!/usr/bin/env bash
# health-check.sh must not report CRITICAL for a healthy base stack, and must
# still catch real failures (router unhealthy, selected relay missing, docker
# daemon down, resolver failure/malformed output).
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
HC_SRC="${HC_SOURCE_OVERRIDE:-$ROOT_DIR/scripts/health-check.sh}"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
[[ -f "$HC_SRC" ]] || { echo "health-check.sh not found at $HC_SRC"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Fixture layout ──────────────────────────────────────────────────────────
# TMP/source/scripts/health-check.sh   (copy of the real script)
# TMP/source/scripts/resolve-compose-stack.sh  (fake resolver)
# TMP/source/lib -> ROOT_DIR/lib       (real registry + safe-env)
# TMP/source/extensions -> ROOT_DIR/extensions  (real registry data)
# TMP/install/.env                     (controlled)
# TMP/install/extensions -> ROOT_DIR/extensions (real relay .disabled files)
# TMP/bin/{docker,curl}                (stubs)
mkdir -p "$TMP/source/scripts" "$TMP/install" "$TMP/bin"
cp "$HC_SRC" "$TMP/source/scripts/health-check.sh"
chmod +x "$TMP/source/scripts/health-check.sh"
ln -s "$ROOT_DIR/lib" "$TMP/source/lib"
ln -s "$ROOT_DIR/extensions" "$TMP/source/extensions"
ln -s "$ROOT_DIR/extensions" "$TMP/install/extensions"
: > "$TMP/install/.env"

# Fake resolver: emits a JSON selection. RESOLVER_MODE controls behavior.
cat > "$TMP/source/scripts/resolve-compose-stack.sh" <<'STUB'
#!/usr/bin/env bash
set -u
mode="${RESOLVER_MODE:-ok}"
case "$mode" in
    fail)
        echo "resolver: simulated failure" >&2
        exit 1
        ;;
    malformed)
        echo 'not-json{'
        exit 0
        ;;
esac
frags=("docker-compose.base.yml")
if [[ "${SELECT_RELAY:-0}" == "1" ]]; then
    frags+=("extensions/services/pixel-model-relay/compose.yaml.disabled")
fi
python3 - "${frags[@]}" <<'PY'
import json, sys
print(json.dumps({"fragments": list(sys.argv[1:])}))
PY
STUB
chmod +x "$TMP/source/scripts/resolve-compose-stack.sh"

# docker stub: returns State.Status, or State.Status + Health when the format
# string asks for it. Never mutates anything. DOCKER_MODE=down simulates a
# missing daemon (all inspect calls fail).
cat > "$TMP/bin/docker" <<'STUB'
#!/bin/sh
if [ "${DOCKER_MODE:-up}" = "down" ]; then
    echo "Cannot connect to the Docker daemon" >&2
    exit 1
fi
name=""
for arg in "$@"; do name="$arg"; done
case "$name" in
    ods-llama-server|ods-webui|ods-dashboard|ods-dashboard-api) state=running; health=none ;;
    ods-model-router) state="${ROUTER_STATE:-running}"; health="${ROUTER_HEALTH:-healthy}" ;;
    ods-pixel-model-relay) state="${RELAY_STATE:-}"; health=none ;;
    *) state=""; health=none ;;
esac
if [ -z "$state" ]; then
    echo "Error: No such object: $name" >&2
    exit 1
fi
case "$*" in
    *State.Status*Health*) echo "$state $health" ;;
    *Health*) echo "$health" ;;
    *) echo "$state" ;;
esac
STUB

# curl stub: inference + base stack ports + relay port 4006 answer; all else
# fails. RELAY_HTTP=down makes 4006 fail even when the container is running.
cat > "$TMP/bin/curl" <<'STUB'
#!/bin/sh
url=""
for arg in "$@"; do
    case "$arg" in http://*) url="$arg" ;; esac
done
case "$url" in
    */v1/completions) echo '{"choices":[{"text":"Hi"}]}' ;;
    http://127.0.0.1:3000/*|http://127.0.0.1:3001/*|http://127.0.0.1:3002/*) echo ok ;;
    http://127.0.0.1:4006/*)
        if [ "${RELAY_HTTP:-up}" = "down" ]; then exit 7; fi
        echo ok
        ;;
    *) exit 7 ;;
esac
STUB
chmod +x "$TMP/bin/docker" "$TMP/bin/curl"

# ── Runner ──────────────────────────────────────────────────────────────────
# Runs the copied health-check.sh under the fixture. Captures the exit code
# despite set -e in the script (we run it in a subshell and record $?).
run_hc() {
    local out="$TMP/out.json" err="$TMP/err.txt"
    : > "$out"; : > "$err"
    (
        set +e
        env \
            INSTALL_DIR="$TMP/install" \
            LLM_PORT=11434 \
            TIMEOUT=1 \
            PATH="$TMP/bin:$PATH" \
            RESOLVER_MODE="${RESOLVER_MODE:-ok}" \
            SELECT_RELAY="${SELECT_RELAY:-0}" \
            DOCKER_MODE="${DOCKER_MODE:-up}" \
            ROUTER_STATE="${ROUTER_STATE:-running}" \
            ROUTER_HEALTH="${ROUTER_HEALTH:-healthy}" \
            RELAY_STATE="${RELAY_STATE:-}" \
            RELAY_HTTP="${RELAY_HTTP:-up}" \
            ENABLE_OPEN_WEBUI="${ENABLE_OPEN_WEBUI:-true}" \
            "$BASH" "$TMP/source/scripts/health-check.sh" --json > "$out" 2> "$err"
        echo $? > "$TMP/rc"
    )
    RESULT="$(cat "$TMP/rc")"
}

# Assert JSON status + exit code + per-service results.
# Usage: assert_hc <label> <expected_status> <expected_rc> <sid=val>...
assert_hc() {
    local label="$1" want_status="$2" want_rc="$3"; shift 3
    local got
    got="$(python3 - "$TMP/out.json" "$RESULT" "$want_status" "$want_rc" "$@" <<'PY'
import json, sys
path, rc, want_status, want_rc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
pairs = sys.argv[5:]
try:
    doc = json.load(open(path))
except Exception as e:
    print(f"BADJSON:{e}")
    raise SystemExit(0)
services = doc.get("services", {})
problems = []
if (doc.get("status") == "critical" if want_status == "not-critical" else doc.get("status") != want_status):
    problems.append(f"status={doc.get('status')!r} want {want_status!r}")
if (rc not in ("0", "1") if want_rc == "noncritical" else rc != want_rc):
    problems.append(f"rc={rc} want {want_rc}")
for p in pairs:
    sid, _, want = p.partition("=")
    got = services.get(sid, "absent")
    if got != want:
        problems.append(f"{sid}={got!r} want {want!r}")
if problems:
    print("MISMATCH: " + "; ".join(problems))
else:
    print("OK")
PY
)"
    if [[ "$got" == "OK" ]]; then
        pass "$label"
    else
        fail "$label ($got)"
    fi
}

# ── Tests ───────────────────────────────────────────────────────────────────

echo "Test 1: default healthy stack (Open WebUI enabled) is HEALTHY"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "default healthy stack" not-critical noncritical \
    dashboard=ok dashboard-api=ok open-webui=ok model-router=ok \
    pixel-model-relay=absent pixel-agent=absent pixel-edge=absent

echo "Test 2: ENABLE_OPEN_WEBUI=false skips the open-webui probe"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=false run_hc
assert_hc "open-webui disabled is skipped" not-critical noncritical \
    open-webui=absent dashboard=ok model-router=ok

echo "Test 3: selected relay missing => CRITICAL"
RESOLVER_MODE=ok SELECT_RELAY=1 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "selected relay missing is critical" critical 2 \
    pixel-model-relay=fail

echo "Test 4: selected relay running on 4006 => HEALTHY"
RESOLVER_MODE=ok SELECT_RELAY=1 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE=running RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "selected relay running is healthy" not-critical noncritical \
    pixel-model-relay=ok

echo "Test 5: unselected relay missing is not reported"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "unselected relay absent" not-critical noncritical \
    pixel-model-relay=absent

echo "Test 6: router running+healthy => ok"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_HEALTH=healthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "router healthy" not-critical noncritical model-router=ok

echo "Test 7: router running+unhealthy => CRITICAL"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_HEALTH=unhealthy \
    RELAY_STATE="" RELAY_HTTP=up ENABLE_OPEN_WEBUI=true run_hc
assert_hc "router unhealthy is critical" critical 2 model-router=fail

echo "Test 8: router exited+healthy => CRITICAL"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=up ROUTER_STATE=exited \
    ROUTER_HEALTH=healthy RELAY_STATE="" RELAY_HTTP=up \
    ENABLE_OPEN_WEBUI=true run_hc
assert_hc "router exited is critical" critical 2 model-router=fail

echo "Test 9: docker daemon unavailable => CRITICAL (no false healthy)"
RESOLVER_MODE=ok SELECT_RELAY=0 DOCKER_MODE=down ROUTER_STATE=running \
    ROUTER_HEALTH=healthy RELAY_STATE="" RELAY_HTTP=up \
    ENABLE_OPEN_WEBUI=true run_hc
assert_hc "docker daemon down is critical" critical 2 model-router=fail

echo "Test 10: resolver failure => stack_selection fail, CRITICAL"
RESOLVER_MODE=fail SELECT_RELAY=0 DOCKER_MODE=up ROUTER_STATE=running \
    ROUTER_HEALTH=healthy RELAY_STATE="" RELAY_HTTP=up \
    ENABLE_OPEN_WEBUI=true run_hc
assert_hc "resolver failure is critical" critical 2 stack_selection=fail

echo "Test 11: resolver malformed JSON => stack_selection fail, CRITICAL"
RESOLVER_MODE=malformed SELECT_RELAY=0 DOCKER_MODE=up ROUTER_STATE=running \
    ROUTER_HEALTH=healthy RELAY_STATE="" RELAY_HTTP=up \
    ENABLE_OPEN_WEBUI=true run_hc
assert_hc "resolver malformed is critical" critical 2 stack_selection=fail

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
