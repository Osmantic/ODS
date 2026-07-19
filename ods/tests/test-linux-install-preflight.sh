#!/usr/bin/env bash
# Tests for scripts/linux-install-preflight.sh (static + JSON contract; no Docker required for schema).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LP="$ROOT_DIR/scripts/linux-install-preflight.sh"
ROOT_PREFLIGHT="$ROOT_DIR/ods-preflight.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   linux-install-preflight.sh tests                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$LP" ]]; then
    fail "linux-install-preflight.sh missing at $LP"
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi
pass "linux-install-preflight.sh exists"

if bash -n "$LP" 2>/dev/null; then
    pass "bash -n syntax check passes"
else
    fail "bash -n syntax check failed"
fi

if grep -q 'set -euo pipefail' "$LP"; then
    pass "set -euo pipefail present"
else
    fail "set -euo pipefail missing"
fi

if grep -q 'schema_version' "$LP" && grep -q 'linux-install-preflight' "$LP"; then
    pass "JSON report kind/schema referenced in script"
else
    fail "Missing schema_version or kind in emitter"
fi

# JSON contract: required top-level keys
JSON_OUT="$(mktemp)"
trap 'rm -f "$JSON_OUT"' EXIT
if "$LP" --json >"$JSON_OUT" 2>/dev/null || true; then
    :
fi
if command -v python3 >/dev/null 2>&1; then
    if python3 - <<PY
import json, sys
path = "$JSON_OUT"
with open(path, encoding="utf-8") as f:
    r = json.load(f)
assert r.get("kind") == "linux-install-preflight"
assert r.get("schema_version") == "1"
assert "checks" in r and isinstance(r["checks"], list)
assert "summary" in r
for k in ("pass", "warn", "fail", "exit_ok"):
    assert k in r["summary"]
for c in r["checks"]:
    assert "id" in c and "status" in c and "message" in c
    assert c["status"] in ("pass", "warn", "fail")
assert "distro" in r and "kernel" in r
print("ok")
PY
    then
        pass "JSON output matches contract (kind, schema, checks, summary)"
    else
        fail "JSON output contract validation failed"
    fi
else
    fail "python3 not available — skipped JSON contract (unexpected on CI)"
fi

# Podman compatibility shims are intentionally not accepted as Docker Engine.
if command -v python3 >/dev/null 2>&1; then
    PODMAN_TMP="$(mktemp -d)"
    cat >"$PODMAN_TMP/docker" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version)
    echo "podman version 5.0.0"
    ;;
  *)
    echo "podman shim called: $*" >&2
    exit 125
    ;;
esac
EOF
    chmod +x "$PODMAN_TMP/docker"
    PODMAN_JSON="$PODMAN_TMP/report.json"
    if PATH="$PODMAN_TMP:$PATH" "$LP" --json >"$PODMAN_JSON" 2>/dev/null || true; then
        :
    fi
    if python3 - <<PY
import json
path = "$PODMAN_JSON"
with open(path, encoding="utf-8") as f:
    report = json.load(f)
checks = {c["id"]: c for c in report["checks"]}
assert checks["DOCKER_INSTALLED"]["status"] == "pass"
assert checks["DOCKER_ENGINE"]["status"] == "fail"
assert checks["DOCKER_DAEMON"]["status"] == "fail"
assert checks["COMPOSE_CLI"]["status"] == "fail"
assert "Podman" in checks["DOCKER_ENGINE"]["message"]
assert report["summary"]["exit_ok"] is False
print("ok")
PY
    then
        pass "Podman docker shim fails loud as unsupported runtime"
    else
        fail "Podman docker shim did not produce the expected fail-loud checks"
    fi
    rm -rf "$PODMAN_TMP"
else
    fail "python3 not available - skipped Podman shim contract (unexpected on CI)"
fi

# ROOTLESS_SUBUID: check referenced in the script
if grep -q 'ROOTLESS_SUBUID' "$LP" && grep -q 'SUBUID_FILE' "$LP" && grep -q 'SUBGID_FILE' "$LP"; then
    pass "ROOTLESS_SUBUID check and SUBUID_FILE/SUBGID_FILE overrides present in script"
else
    fail "ROOTLESS_SUBUID check or file-path overrides missing from script"
fi

# ROOTLESS_SUBUID: rootless Docker with sufficient ranges → pass
if command -v python3 >/dev/null 2>&1; then
    _RTMP="$(mktemp -d)"
    # Mock docker that reports rootless mode in 'docker info'
    cat >"$_RTMP/docker" <<'MOCKEOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "Docker version 24.0.5, build ced0996" ;;
  info)
    printf 'Server:\n Security Options:\n  seccomp\n  rootless\n  cgroupns\n'
    ;;
  compose)
    case "${2:-}" in
      version) echo "Docker Compose version v2.20.0" ;;
      *) exit 0 ;;
    esac
    ;;
  *) exit 0 ;;
esac
MOCKEOF
    chmod +x "$_RTMP/docker"
    _CURRENT_USER="$(id -un)"
    printf '%s:100000:65536\n' "$_CURRENT_USER" >"$_RTMP/subuid"
    printf '%s:100000:65536\n' "$_CURRENT_USER" >"$_RTMP/subgid"
    _R_JSON="$_RTMP/report.json"
    SUBUID_FILE="$_RTMP/subuid" SUBGID_FILE="$_RTMP/subgid" \
        PATH="$_RTMP:$PATH" "$LP" --json >"$_R_JSON" 2>/dev/null || true
    if python3 - <<PY
import json
with open("$_R_JSON", encoding="utf-8") as f:
    report = json.load(f)
checks = {c["id"]: c for c in report["checks"]}
c = checks.get("ROOTLESS_SUBUID")
assert c is not None, "ROOTLESS_SUBUID check missing"
assert c["status"] == "pass", f"expected pass, got {c['status']}: {c['message']}"
print("ok")
PY
    then
        pass "ROOTLESS_SUBUID: rootless Docker with valid ranges → pass"
    else
        fail "ROOTLESS_SUBUID: rootless Docker with valid ranges should be pass"
    fi
    rm -rf "$_RTMP"
else
    fail "python3 not available — skipped ROOTLESS_SUBUID valid-ranges test"
fi

# ROOTLESS_SUBUID: rootless Docker with insufficient range → fail
if command -v python3 >/dev/null 2>&1; then
    _RTMP="$(mktemp -d)"
    cat >"$_RTMP/docker" <<'MOCKEOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "Docker version 24.0.5, build ced0996" ;;
  info)
    printf 'Server:\n Security Options:\n  seccomp\n  rootless\n  cgroupns\n'
    ;;
  compose)
    case "${2:-}" in
      version) echo "Docker Compose version v2.20.0" ;;
      *) exit 0 ;;
    esac
    ;;
  *) exit 0 ;;
esac
MOCKEOF
    chmod +x "$_RTMP/docker"
    _CURRENT_USER="$(id -un)"
    # Deliberately below the 65536 minimum
    printf '%s:100000:100\n' "$_CURRENT_USER" >"$_RTMP/subuid"
    printf '%s:100000:100\n' "$_CURRENT_USER" >"$_RTMP/subgid"
    _R_JSON="$_RTMP/report.json"
    SUBUID_FILE="$_RTMP/subuid" SUBGID_FILE="$_RTMP/subgid" \
        PATH="$_RTMP:$PATH" "$LP" --json >"$_R_JSON" 2>/dev/null || true
    if python3 - <<PY
import json
with open("$_R_JSON", encoding="utf-8") as f:
    report = json.load(f)
checks = {c["id"]: c for c in report["checks"]}
c = checks.get("ROOTLESS_SUBUID")
assert c is not None, "ROOTLESS_SUBUID check missing"
assert c["status"] == "fail", f"expected fail, got {c['status']}: {c['message']}"
assert "remediation" in c and len(c["remediation"]) > 0, "remediation missing"
print("ok")
PY
    then
        pass "ROOTLESS_SUBUID: rootless Docker with insufficient range → fail + remediation"
    else
        fail "ROOTLESS_SUBUID: rootless Docker with insufficient range should be fail"
    fi
    rm -rf "$_RTMP"
else
    fail "python3 not available — skipped ROOTLESS_SUBUID insufficient-range test"
fi

# ROOTLESS_SUBUID: non-rootless Docker → pass (check not applicable)
if command -v python3 >/dev/null 2>&1; then
    _RTMP="$(mktemp -d)"
    cat >"$_RTMP/docker" <<'MOCKEOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "Docker version 24.0.5, build ced0996" ;;
  info)
    # No 'rootless' line — standard daemon mode
    printf 'Server:\n Security Options:\n  seccomp\n   Profile: builtin\n  cgroupns\n'
    ;;
  compose)
    case "${2:-}" in
      version) echo "Docker Compose version v2.20.0" ;;
      *) exit 0 ;;
    esac
    ;;
  *) exit 0 ;;
esac
MOCKEOF
    chmod +x "$_RTMP/docker"
    _R_JSON="$_RTMP/report.json"
    PATH="$_RTMP:$PATH" "$LP" --json >"$_R_JSON" 2>/dev/null || true
    if python3 - <<PY
import json
with open("$_R_JSON", encoding="utf-8") as f:
    report = json.load(f)
checks = {c["id"]: c for c in report["checks"]}
c = checks.get("ROOTLESS_SUBUID")
assert c is not None, "ROOTLESS_SUBUID check missing"
assert c["status"] == "pass", f"expected pass, got {c['status']}: {c['message']}"
print("ok")
PY
    then
        pass "ROOTLESS_SUBUID: non-rootless Docker → pass (not applicable)"
    else
        fail "ROOTLESS_SUBUID: non-rootless Docker should be pass"
    fi
    rm -rf "$_RTMP"
else
    fail "python3 not available — skipped ROOTLESS_SUBUID non-rootless test"
fi

# ods-preflight.sh delegates --install-env to linux-install-preflight
if grep -q 'linux-install-preflight.sh' "$ROOT_PREFLIGHT" && grep -q '\-\-install-env' "$ROOT_PREFLIGHT"; then
    pass "ods-preflight.sh delegates --install-env to linux-install-preflight.sh"
else
    fail "ods-preflight.sh missing --install-env delegation"
fi

if bash -n "$ROOT_PREFLIGHT" 2>/dev/null; then
    pass "ods-preflight.sh still passes bash -n"
else
    fail "ods-preflight.sh bash -n failed after edit"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
