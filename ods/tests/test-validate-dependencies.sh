#!/usr/bin/env bash
# Coverage for lib/validate-dependencies.sh.
#
# installers/phases/11-services.sh calls validate_service_dependencies() right
# before bringing the stack up and aborts the install when it fails, so the set
# of names it treats as "enabled" has to be exactly the services that exist.
#
# Run: bash tests/test-validate-dependencies.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT_DIR/lib/validate-dependencies.sh"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; echo "       $2"; FAIL=$((FAIL + 1)); }

check_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "$label"
    else
        fail "$label" "expected [$expected] got [$actual]"
    fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# A base compose shaped like the real one: a YAML anchor whose keys sit at the
# same indent as service names, then services, then a networks block.
cat > "$WORKDIR/docker-compose.base.yml" <<'EOF'
name: ods

x-logging: &default-logging
  driver: "json-file"
  options:
    max-size: "10m"

services:
  llama-server:
    image: example/llama:1
  dashboard:
    image: example/dashboard:1
  dashboard-api:
    image: example/dashboard-api:1

networks:
  default:
    name: ods
EOF

# Drive the real function with a fabricated registry.
run_validation() {
    local depends="$1"
    INSTALL_DIR="$WORKDIR" bash -c '
        set -uo pipefail
        declare -a SERVICE_IDS=("my-ext")
        declare -A SERVICE_COMPOSE=(["my-ext"]="'"$WORKDIR"'/my-ext-compose.yaml")
        declare -A SERVICE_DEPENDS=(["my-ext"]="'"$depends"'")
        . "'"$LIB"'"
        validate_service_dependencies
        echo "rc=$?"
    ' 2>&1
}

: > "$WORKDIR/my-ext-compose.yaml"

# ── 1. Real core services satisfy a dependency ────────────────────────────

OUT="$(run_validation "llama-server dashboard-api")"
check_eq "core services in the services block are accepted" "rc=0" "$(grep -o 'rc=[0-9]*' <<< "$OUT")"

# ── 2. Names outside the services block are not services ──────────────────
#
# The regression: the core-service list came from every two-space-indented key
# in the file, so the x-logging anchor's `driver`/`options` keys and the
# `default` network were accepted as satisfied dependencies.

for phantom in driver options default; do
    OUT="$(run_validation "$phantom")"
    check_eq "'$phantom' is not treated as an enabled service" "rc=1" "$(grep -o 'rc=[0-9]*' <<< "$OUT")"
done

# ── 3. A genuinely missing service is still reported ──────────────────────

OUT="$(run_validation "not-a-service")"
check_eq "unknown dependency fails validation" "rc=1" "$(grep -o 'rc=[0-9]*' <<< "$OUT")"
if grep -q "depends on 'not-a-service'" <<< "$OUT"; then
    pass "unknown dependency names the missing service"
else
    fail "unknown dependency names the missing service" "$OUT"
fi

# ── 4. No dependencies declared ───────────────────────────────────────────

OUT="$(run_validation "")"
check_eq "no dependencies passes" "rc=0" "$(grep -o 'rc=[0-9]*' <<< "$OUT")"

# ── 5. The real repo's core services are all recognised ───────────────────
#
# Guards against the extraction drifting away from docker-compose.base.yml as
# services are added or the file is restructured.

# Run from ROOT_DIR with a relative path: an interpreter that does not share
# this shell's path conventions still resolves the file.
REAL_SERVICES="$(cd "$ROOT_DIR" && python3 -c "
import yaml
data = yaml.safe_load(open('docker-compose.base.yml', encoding='utf-8'))
print(' '.join(sorted(data.get('services', {}))))
" 2>/dev/null || echo "")"

if [[ -z "$REAL_SERVICES" ]]; then
    echo "[SKIP] real base compose check (PyYAML unavailable)"
else
    OUT="$(INSTALL_DIR="$ROOT_DIR" bash -c '
        set -uo pipefail
        declare -a SERVICE_IDS=("my-ext")
        declare -A SERVICE_COMPOSE=(["my-ext"]="'"$WORKDIR"'/my-ext-compose.yaml")
        declare -A SERVICE_DEPENDS=(["my-ext"]="'"$REAL_SERVICES"'")
        . "'"$LIB"'"
        validate_service_dependencies
        echo "rc=$?"
    ' 2>&1)"
    check_eq "every service in the real base compose is recognised" "rc=0" "$(grep -o 'rc=[0-9]*' <<< "$OUT")"
fi

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] service dependency validation"
