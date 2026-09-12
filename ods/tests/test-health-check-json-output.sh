#!/usr/bin/env bash
# `health-check.sh --json` must write only the JSON document to stdout.
#
# The human banner and per-check lines go through log(), which was gated on
# --quiet alone, so `--json` printed "ODS Health Check", the check table and
# then the JSON -- and any consumer parsing stdout failed on line 1.
#
# Run from repo root:  bash ods/tests/test-health-check-json-output.sh
# Or from ods:         bash tests/test-health-check-json-output.sh
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
# Fixture install: only .env is read from INSTALL_DIR; the registry and libs
# come from the checkout. Every probe targets loopback and fails fast.
mkdir -p "$TMP/install"
printf 'OLLAMA_PORT=1\nWEBUI_PORT=1\n' > "$TMP/install/.env"
# A docker CLI whose daemon is unreachable, so container checks fail cleanly.
mkdir -p "$TMP/bin"
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$TMP/bin/docker"
chmod +x "$TMP/bin/docker"

echo "Test 1: --json stdout parses as a JSON document"
set +e
INSTALL_DIR="$TMP/install" TIMEOUT=1 PATH="$TMP/bin:$PATH" "$BASH" "$HC" --json > "$TMP/out.json" 2> "$TMP/err.txt"
rc=$?
set -e
if python3 - "$TMP/out.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
assert isinstance(doc, dict) and "status" in doc and "services" in doc, doc.keys()
PY
then
    pass "--json stdout is a single JSON document (status + services present; exit $rc reflects check results)"
else
    fail "--json stdout is not valid JSON; first line: $(head -n 1 "$TMP/out.json")"
fi

echo "Test 2: no human banner or check lines on stdout in --json mode"
if grep -qE 'ODS Health Check|━━|✓|✗' "$TMP/out.json"; then
    fail "human output found on stdout in --json mode"
else
    pass "stdout carries only the document"
fi

echo "Test 3: default (human) mode still prints the banner"
set +e
human="$(INSTALL_DIR="$TMP/install" TIMEOUT=1 PATH="$TMP/bin:$PATH" "$BASH" "$HC" 2>&1)"
set -e
if [[ "$human" == *"ODS Health Check"* ]]; then
    pass "human mode unchanged"
else
    fail "human mode lost its banner"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
