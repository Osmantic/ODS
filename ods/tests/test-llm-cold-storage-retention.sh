#!/usr/bin/env bash
# Public CLI coverage for configurable cold-storage retention.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/llm-cold-storage.sh"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); }
contains() {
    local label="$1" needle="$2" haystack="$3"
    [[ "$haystack" == *"$needle"* ]] && pass "$label" || fail "$label" "missing $needle"
}

HF_CACHE="$WORKDIR/cache"
COLD_DIR="$WORKDIR/cold"
LOG_FILE="$WORKDIR/logs/cold-storage.log"
mkdir -p "$HF_CACHE/models--Fixture--Unused" "$COLD_DIR"

run_script() {
    HF_CACHE="$HF_CACHE" COLD_DIR="$COLD_DIR" LOG_FILE="$LOG_FILE" \
        bash "$SCRIPT" "$@" 2>&1
}

OUT="$(run_script --max-idle-days 10000)"
contains "custom threshold keeps younger candidates" "SKIP (recent, 9999d)" "$OUT"

OUT="$(run_script --max-idle-days 9000)"
contains "custom threshold selects older candidates" "WOULD ARCHIVE: models--Fixture--Unused" "$OUT"
if [[ -d "$HF_CACHE/models--Fixture--Unused" && ! -L "$HF_CACHE/models--Fixture--Unused" ]]; then
    pass "dry run leaves the model in hot storage"
else
    fail "dry run leaves the model in hot storage" "model moved"
fi

run_script --max-idle-days nope >/dev/null
RC=$?
[[ "$RC" -eq 2 ]] && pass "invalid threshold exits 2" || fail "invalid threshold exits 2" "got $RC"

OUT="$(run_script --help)"
contains "help documents retention override" "--max-idle-days N" "$OUT"

echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
