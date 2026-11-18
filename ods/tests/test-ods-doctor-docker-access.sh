#!/usr/bin/env bash
# ods-doctor must tell a running-but-inaccessible Docker daemon (the account
# is not yet in an active docker group, as right after install) apart from a
# stopped daemon. A stub docker on PATH plays each daemon; no Docker needed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"

PASSED=0
FAILED=0
pass() { printf "  ✓ PASS %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ✗ FAIL %s\n" "$1"; FAILED=$((FAILED + 1)); }

if ! command -v python3 >/dev/null 2>&1; then
    echo "[SKIP] python3 not available — doctor cannot run here"
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# $1 = name, $2 = docker info stderr
make_docker() {
    mkdir -p "$TMP_DIR/$1"
    cat >"$TMP_DIR/$1/docker" <<STUB
#!/bin/sh
case "\$1" in
    info) echo "$2" >&2; exit 1 ;;
    compose) [ "\$2" = version ] && { echo "Docker Compose version v2.40.0"; exit 0; } ;;
esac
exit 1
STUB
    chmod +x "$TMP_DIR/$1/docker"
}

# Prints: <docker_daemon> <docker_access_denied> <group-hint> <start-hint>
run_doctor() {
    local report="$TMP_DIR/$1.json"
    PATH="$TMP_DIR/$1:$PATH" bash "$DOCTOR" "$report" >"$TMP_DIR/$1.out" 2>&1 || true
    [[ -f "$report" ]] || { echo "missing missing missing missing"; return; }
    python3 - "$report" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
runtime = report["runtime"]
hints = report.get("autofix_hints", [])
print(runtime["docker_daemon"], bool(runtime.get("docker_access_denied")),
      any("newgrp docker" in h for h in hints),
      any("Start Docker daemon" in h for h in hints))
PY
}

make_docker denied "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock"
make_docker stopped "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"

read -r daemon denied group_hint start_hint < <(run_doctor denied)
if [[ "$daemon $denied $group_hint $start_hint" == "False True True False" ]]; then
    pass "permission denied is reported as missing docker group access, not a stopped daemon"
else
    fail "permission denied: daemon=$daemon access_denied=$denied group_hint=$group_hint start_hint=$start_hint"
fi
if grep -q "Docker access: permission denied" "$TMP_DIR/denied.out"; then
    pass "summary names the permission problem"
else
    fail "summary does not name the permission problem"
fi

read -r daemon denied group_hint start_hint < <(run_doctor stopped)
if [[ "$daemon $denied $group_hint $start_hint" == "False False False True" ]]; then
    pass "a stopped daemon keeps the start-the-daemon hint"
else
    fail "stopped daemon: daemon=$daemon access_denied=$denied group_hint=$group_hint start_hint=$start_hint"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ "$FAILED" -eq 0 ]]
