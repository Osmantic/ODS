#!/bin/bash
# ============================================================================
# Regression: two enabled user extensions claiming the same container_name or
# host port must not take the whole stack down.
#
# The resolver used to check user extensions only against core services, so
# it emitted both colliding compose files. `docker compose config` then failed
# with 'container name "..." is already in use' (or `up` with "port already
# allocated"), and no service, core included, could start. The later
# extension in sorted id order must be skipped with a WARNING instead.
#
# Usage: ./tests/test-resolve-compose-extension-collisions.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo -e "  ${YELLOW}⊘ SKIP${NC} python3 + PyYAML required"
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

printf 'services:\n  base-service:\n    image: nginx:latest\n' > "$TMP_DIR/docker-compose.base.yml"
printf 'services:\n  base-service:\n    image: nginx:latest\n' > "$TMP_DIR/docker-compose.nvidia.yml"

add_user_ext() {  # $1 = id, $2 = compose content
    mkdir -p "$TMP_DIR/data/user-extensions/$1"
    printf 'schema_version: ods.services.v1\nservice:\n  gpu_backends: ["all"]\n  compose_file: compose.yaml\n' \
        > "$TMP_DIR/data/user-extensions/$1/manifest.yaml"
    printf '%b' "$2" > "$TMP_DIR/data/user-extensions/$1/compose.yaml"
}

add_user_ext ext-a 'services:\n  ext-a:\n    image: busybox\n    container_name: ods-shared\n    ports: ["127.0.0.1:9911:80"]\n'
add_user_ext ext-b 'services:\n  ext-b:\n    image: busybox\n    container_name: ods-shared\n'
add_user_ext ext-c 'services:\n  ext-c:\n    image: busybox\n    ports: ["127.0.0.1:${EXT_C_PORT:-9911}:80"]\n'
add_user_ext ext-d 'services:\n  ext-d:\n    image: busybox\n    container_name: ods-ext-d\n    ports: ["127.0.0.1:9912:80"]\n'

set +e
OUT="$(env -u EXT_C_PORT bash "$RESOLVER" --script-dir "$TMP_DIR" --tier 1 --gpu-backend nvidia 2>"$TMP_DIR/stderr")"
RC=$?
set -e
ERR="$(cat "$TMP_DIR/stderr")"

[[ "$RC" -eq 0 ]] && pass "resolver exits 0" || fail "resolver exited $RC: $ERR"
has() { printf "%s" "$OUT" | grep -qE "$1"; }
has "ext-a[/\]compose.yaml" && pass "first claimant (ext-a) kept" || fail "ext-a dropped"
has "ext-d[/\]compose.yaml" && pass "non-colliding extension (ext-d) kept" || fail "ext-d dropped"
has "ext-b[/\]compose.yaml" && fail "ext-b (duplicate container_name) included" || pass "ext-b skipped"
has "ext-c[/\]compose.yaml" && fail "ext-c (duplicate host port) included" || pass "ext-c skipped"
printf '%s' "$ERR" | grep -q "WARNING: ext-b: container name 'ods-shared' is already used by user extension 'ext-a'" \
    && pass "ext-b skip warns" || fail "missing ext-b warning: $ERR"
printf '%s' "$ERR" | grep -q "WARNING: ext-c: host port '9911' is already used by user extension 'ext-a'" \
    && pass "ext-c skip warns" || fail "missing ext-c warning: $ERR"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    read -r -a FLAGS <<< "$OUT"
    if (cd "$TMP_DIR" && docker compose "${FLAGS[@]}" config -q); then
        pass "merged stack passes docker compose config"
    else
        fail "merged stack fails docker compose config"
    fi
else
    echo -e "  ${YELLOW}⊘ SKIP${NC} docker compose unavailable; asserted the -f list only"
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All extension collision resolver tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}Extension collision resolver tests failed${NC} ($PASSED passed, $FAILED failed)"
    exit 1
fi
