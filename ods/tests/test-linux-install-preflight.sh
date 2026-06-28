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
