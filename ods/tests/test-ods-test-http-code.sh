#!/usr/bin/env bash
# A probe that gets no HTTP response must be reported as "HTTP 000", not
# "HTTP 000000": curl already prints "000" for -w '%{http_code}' when the
# connection fails, and the old `|| echo "000"` inside the command
# substitution appended a second one.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required (service registry)" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_TEST_UNDER_TEST:-$ROOT_DIR/scripts/ods-test.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v curl >/dev/null 2>&1 || fail "curl is required"

INSTALL="$TMP_DIR/install"
mkdir -p "$INSTALL/scripts" "$INSTALL/bin" "$INSTALL/extensions/services"
cp -R "$ROOT_DIR/lib" "$INSTALL/"
cp "$TARGET" "$INSTALL/scripts/ods-test.sh"
for manifest in "$ROOT_DIR"/extensions/services/*/manifest.yaml; do
    svc="$(basename "$(dirname "$manifest")")"
    mkdir -p "$INSTALL/extensions/services/$svc"
    cp "$manifest" "$INSTALL/extensions/services/$svc/"
done
printf 'OLLAMA_PORT=1\nWEBUI_PORT=1\n' > "$INSTALL/.env"
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$INSTALL/bin/docker"
chmod +x "$INSTALL/bin/docker"

# Only the LLM section is needed: its health probe hits a closed port.
out="$TMP_DIR/out.txt"
(
    cd "$INSTALL" || exit 1
    ODS_DIR="$INSTALL" ENV_FILE="$INSTALL/.env" PATH="$INSTALL/bin:$PATH" \
        "$BASH" scripts/ods-test.sh --service llm
) >"$out" 2>&1 || true

grep -q 'LLM Health.*\[FAIL\]' "$out" || fail "the LLM health probe did not fail against a closed port: $(grep 'LLM Health' "$out" | head -n 1)"
if grep -q 'HTTP 000000' "$out"; then
    fail "a failed probe is reported as 'HTTP 000000' (doubled fallback): $(grep 'LLM Health' "$out" | head -n 1)"
fi
grep -q 'LLM Health.*HTTP 000$' "$out" || fail "expected 'HTTP 000' on the failed LLM health line: $(grep 'LLM Health' "$out" | head -n 1)"
pass "a probe with no HTTP response is reported as HTTP 000"
