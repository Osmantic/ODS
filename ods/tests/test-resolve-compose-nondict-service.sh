#!/bin/bash
# ============================================================================
# Regression: resolve-compose-stack.sh must not crash on a manifest whose
# `service:` is a scalar/list instead of a mapping.
#
# The resolver runs on every `ods` invocation. A user-extension manifest with a
# non-dict `service:` used to reach `service.get("gpu_backends", ...)` and raise
# an uncaught AttributeError ('str'/'list' object has no attribute 'get'). That
# escaped the --skip-broken handler (which only classifies YAML/JSON/KeyError/
# TypeError), so one malformed or backup-restored extension bricked every CLI
# command. Such a manifest must be skipped with a warning, like a non-dict
# manifest already is.
#
# Usage: ./tests/test-resolve-compose-nondict-service.sh
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

# Minimal ODS root: base + GPU overlay so resolution has something to emit.
printf 'services:\n  base-service:\n    image: nginx:latest\n' > "$TMP_DIR/docker-compose.base.yml"
printf 'services:\n  base-service:\n    image: nginx:latest\n' > "$TMP_DIR/docker-compose.nvidia.yml"

# A built-in extension whose `service:` is a scalar (malformed).
mkdir -p "$TMP_DIR/extensions/services/badbuiltin"
printf 'schema_version: ods.services.v1\nservice: "oops"\n' \
    > "$TMP_DIR/extensions/services/badbuiltin/manifest.yaml"
printf 'services: {}\n' > "$TMP_DIR/extensions/services/badbuiltin/compose.yaml"

# A user extension whose `service:` is a list (malformed / backup-restored).
mkdir -p "$TMP_DIR/data/user-extensions/baduser"
printf 'schema_version: ods.services.v1\nservice:\n  - not\n  - a\n  - mapping\n' \
    > "$TMP_DIR/data/user-extensions/baduser/manifest.yaml"
printf 'services: {}\n' > "$TMP_DIR/data/user-extensions/baduser/compose.yaml"

# A valid user extension that must still resolve after the bad one is skipped.
mkdir -p "$TMP_DIR/data/user-extensions/gooduser"
printf 'schema_version: ods.services.v1\nservice:\n  gpu_backends: ["all"]\n  compose_file: compose.yaml\n' \
    > "$TMP_DIR/data/user-extensions/gooduser/manifest.yaml"
printf 'services:\n  goodx:\n    image: busybox\n    ports: ["127.0.0.1:9911:9911"]\n' \
    > "$TMP_DIR/data/user-extensions/gooduser/compose.yaml"

run_resolver() {  # "$@" = extra flags; captures combined output in RESOLVER_OUT, exit in RC
    # Disable errexit around the capture: a crashing resolver (the bug under
    # test) exits non-zero, and `var="$(...)"` would otherwise abort this test
    # before it can report the crash.
    set +e
    RESOLVER_OUT="$(bash "$RESOLVER" --script-dir "$TMP_DIR" --tier 1 --gpu-backend nvidia "$@" 2>&1)"
    RC=$?
    set -e
}

check() {  # $1 = needle, $2 = description
    if printf '%s' "$RESOLVER_OUT" | grep -q "$1"; then pass "$2"; else fail "$2"; fi
}

for label in "--skip-broken" "default"; do
    if [[ "$label" == "default" ]]; then run_resolver; else run_resolver "$label"; fi
    if printf '%s' "$RESOLVER_OUT" | grep -qiE 'Traceback|AttributeError'; then
        fail "[$label] resolver crashed on a non-dict service manifest"
        printf '%s\n' "$RESOLVER_OUT" | grep -iE 'Traceback|Error' | head -3 | sed 's/^/      /'
        continue
    fi
    if [[ "$RC" -eq 0 ]]; then
        pass "[$label] resolver exits 0 (no crash)"
    else
        fail "[$label] resolver exited $RC"
    fi
    check "badbuiltin" "[$label] built-in bad manifest reported"
    check "baduser" "[$label] user bad manifest reported"
    check "gooduser/compose.yaml" "[$label] valid user extension still resolved"
done

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All non-dict service resolver tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}Non-dict service resolver tests failed${NC} ($PASSED passed, $FAILED failed)"
    exit 1
fi
