#!/usr/bin/env bash
# macOS `ods config show` / `ods config edit` must work while the Docker
# runtime is down. Both only read or open the local .env, but they went
# through test_install(), whose Docker probe made them exit with
# "Docker Desktop is not running." -- so the one moment a user needs to look
# at or fix .env (the stack will not come up) was the moment they could not.
# Commands that actually talk to compose (status, start, ...) keep the gate.
#
# Run from repo root:  bash ods/tests/test-macos-config-without-docker.sh
# Or from ods:         bash tests/test-macos-config-without-docker.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }

[[ -f "$CLI" ]] || { echo "ods-macos.sh not found at $CLI" >&2; exit 1; }

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# A docker CLI whose daemon is unreachable: `docker info` fails.
SHIM_DIR="$TEMP_DIR/bin"
mkdir -p "$SHIM_DIR"
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$SHIM_DIR/docker"
chmod +x "$SHIM_DIR/docker"

INSTALL_SCAFFOLD="$TEMP_DIR/install"
mkdir -p "$INSTALL_SCAFFOLD"
touch "$INSTALL_SCAFFOLD/docker-compose.base.yml"
printf 'OLLAMA_PORT=11434\nLLM_MODEL=some-model\nDASHBOARD_API_KEY=leak-key-value\n' > "$INSTALL_SCAFFOLD/.env"

run_cli() {
    ODS_HOME="$INSTALL_SCAFFOLD" PATH="$SHIM_DIR:$PATH" EDITOR=true \
        bash "$CLI" "$@" 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
    return "${PIPESTATUS[0]}"
}

echo ""
echo "=== macOS CLI config commands without a Docker runtime ==="
echo ""

out=$(run_cli config show); rc=$?
if [[ $rc -eq 0 && "$out" == *"LLM_MODEL=some-model"* && "$out" != *"Docker Desktop is not running"* ]]; then
    pass "config show works with Docker down (rc=0, .env displayed)"
else
    fail "config show rc=$rc: $(printf '%s' "$out" | head -n 2 | tr '\n' '|')"
fi
if [[ "$out" != *"leak-key-value"* ]]; then
    pass "config show still masks secrets"
else
    fail "config show leaked a secret value"
fi

out=$(run_cli config edit); rc=$?
if [[ $rc -eq 0 && "$out" != *"Docker Desktop is not running"* ]]; then
    pass "config edit works with Docker down (EDITOR=true, rc=0)"
else
    fail "config edit rc=$rc: $(printf '%s' "$out" | head -n 2 | tr '\n' '|')"
fi

out=$(run_cli status); rc=$?
if [[ $rc -ne 0 && "$out" == *"Docker Desktop is not running"* ]]; then
    pass "status still requires a Docker runtime (gate unchanged)"
else
    fail "status rc=$rc should still be gated on Docker: $(printf '%s' "$out" | head -n 2 | tr '\n' '|')"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
