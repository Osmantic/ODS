#!/bin/bash
# ============================================================================
# ODS Windows report command tests
# ============================================================================
# Static checks for the Windows ods.ps1 "report" command wiring.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"
REPORT_LIB="$ROOT_DIR/installers/windows/lib/install-report.ps1"
LLM_HELPER_LIB="$ROOT_DIR/installers/windows/lib/llm-endpoint.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Windows report command tests ==="
echo ""

[[ -f "$ODS_PS1" ]] && pass "ods.ps1 exists" || fail "ods.ps1 missing"
[[ -f "$REPORT_LIB" ]] && pass "install-report.ps1 exists" || fail "install-report.ps1 missing"
[[ -f "$LLM_HELPER_LIB" ]] && pass "llm-endpoint.ps1 exists" || fail "llm-endpoint.ps1 missing"

grep -q "install-report.ps1" "$ODS_PS1" && pass "ods.ps1 sources report library" || fail "ods.ps1 missing report library source"
grep -q "llm-endpoint.ps1" "$ODS_PS1" && pass "ods.ps1 sources llm endpoint helper" || fail "ods.ps1 missing llm endpoint helper source"
grep -q "function Invoke-Report" "$ODS_PS1" && pass "Invoke-Report function exists" || fail "Invoke-Report function missing"
grep -q '"report"  { Invoke-Report }' "$ODS_PS1" && pass "report command dispatch exists" || fail "report command dispatch missing"
grep -q "Generate Windows diagnostics bundle" "$ODS_PS1" && pass "help text includes report command" || fail "help text missing report command"

grep -q "function Write-ODSInstallReport" "$REPORT_LIB" && pass "report writer function exists" || fail "report writer function missing"
grep -q "windows-report" "$REPORT_LIB" && pass "report output path is defined" || fail "report output path missing"
grep -q "Get-WindowsLocalLlmEndpoint" "$REPORT_LIB" && pass "report uses shared llm endpoint helper" || fail "report missing shared llm endpoint helper"
if grep -q 'http://localhost:8080/health' "$REPORT_LIB"; then
    fail "report lib still hardcodes localhost:8080/health"
else
    pass "report lib does not hardcode localhost:8080/health"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
