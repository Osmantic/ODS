#!/usr/bin/env bash
# Static checks for voice doctor/repair command wiring.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"
ODS_DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"
WINDOWS_CLI="$ROOT_DIR/installers/windows/ods.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Voice repair command tests ==="
echo ""

[[ -f "$ODS_CLI" ]] && pass "ods-cli exists" || fail "ods-cli missing"
[[ -f "$ODS_DOCTOR" ]] && pass "ods-doctor.sh exists" || fail "ods-doctor.sh missing"
[[ -f "$WINDOWS_CLI" ]] && pass "Windows ods.ps1 exists" || fail "Windows ods.ps1 missing"

grep -q '^cmd_repair()' "$ODS_CLI" && pass "ods-cli defines cmd_repair" || fail "cmd_repair missing"
grep -q 'cmd_stt download' "$ODS_CLI" && pass "repair reuses STT download command" || fail "repair does not cache STT model"
grep -q 'Starting voice services' "$ODS_CLI" && pass "repair starts voice services" || fail "repair does not start voice services"
grep -q 'Voice Readiness' "$ODS_CLI" && pass "doctor displays voice readiness" || fail "doctor voice readiness missing"
grep -q 'repair|fix \[voice\]' "$ODS_CLI" && pass "help documents repair voice" || fail "help missing repair voice"

grep -q '"tts_http"' "$ODS_DOCTOR" && pass "doctor report includes TTS status" || fail "doctor missing TTS status"
grep -q 'ods repair voice' "$ODS_DOCTOR" && pass "doctor suggests repair voice" || fail "doctor missing repair hint"

grep -q 'function Invoke-Doctor' "$WINDOWS_CLI" && pass "Windows CLI defines doctor" || fail "Windows doctor missing"
grep -q 'function Invoke-RepairVoice' "$WINDOWS_CLI" && pass "Windows CLI defines repair voice" || fail "Windows repair voice missing"
grep -q '".*doctor".*Invoke-Doctor' "$WINDOWS_CLI" && pass "Windows dispatch includes doctor" || fail "Windows doctor dispatch missing"
grep -q '".*repair".*Invoke-Repair' "$WINDOWS_CLI" && pass "Windows dispatch includes repair" || fail "Windows repair dispatch missing"
grep -q 'repair voice' "$WINDOWS_CLI" && pass "Windows help documents repair voice" || fail "Windows help missing repair voice"

if bash -n "$ODS_CLI" 2>/dev/null; then
    pass "ods-cli syntax valid"
else
    fail "ods-cli syntax invalid"
fi

if bash -n "$ODS_DOCTOR" 2>/dev/null; then
    pass "ods-doctor syntax valid"
else
    fail "ods-doctor syntax invalid"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
