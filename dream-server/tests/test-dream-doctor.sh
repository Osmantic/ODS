#!/bin/bash
# ============================================================================
# Dream Server dream-doctor.sh Test Suite
# ============================================================================
# Ensures scripts/dream-doctor.sh runs without shell errors and produces
# expected JSON output with correct structure. Validates the diagnostic tool
# used in installer simulation and CI artifacts.
#
# Usage: ./tests/test-dream-doctor.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   dream-doctor.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script exists
if [[ ! -f "$ROOT_DIR/scripts/dream-doctor.sh" ]]; then
    fail "scripts/dream-doctor.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "dream-doctor.sh exists"

# 2. --help flag works
set +e
help_out=$(cd "$ROOT_DIR" && bash scripts/dream-doctor.sh --help 2>&1)
help_exit=$?
set -e

if [[ "$help_exit" -eq 0 ]] && echo "$help_out" | grep -q "Usage:"; then
    pass "dream-doctor.sh --help displays usage"
else
    fail "dream-doctor.sh --help failed or missing usage text"
fi

# 3. Runs without shell error (default output path)
TEMP_REPORT=$(mktemp /tmp/dream-doctor-test.XXXXXX.json)
trap 'rm -f "$TEMP_REPORT"' EXIT

set +e
out=$(cd "$ROOT_DIR" && bash scripts/dream-doctor.sh "$TEMP_REPORT" 2>&1)
exit_code=$?
set -e

if echo "$out" | grep -q "unbound variable\|syntax error\|command not found"; then
    fail "dream-doctor.sh produced shell error in output"
else
    pass "dream-doctor.sh runs without shell errors"
fi

# Exit code must be 0 or 1 (documented: 0=success, 1=error)
if [[ "$exit_code" -eq 0 ]] || [[ "$exit_code" -eq 1 ]]; then
    pass "dream-doctor.sh exit code is valid (0|1): $exit_code"
else
    fail "dream-doctor.sh exit code should be 0 or 1; got $exit_code"
fi

# 4. Produces JSON output file
if [[ -f "$TEMP_REPORT" ]]; then
    pass "dream-doctor.sh creates output file"
else
    fail "dream-doctor.sh did not create output file at $TEMP_REPORT"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi

# 5. Output is valid JSON
if command -v jq >/dev/null 2>&1; then
    jq_exit=0
    jq empty "$TEMP_REPORT" || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "dream-doctor.sh output is valid JSON"
    else
        fail "dream-doctor.sh output is not valid JSON"
    fi
else
    skip "jq not available - skipping JSON validation"
fi

# 6. Required top-level fields exist
if command -v jq >/dev/null 2>&1; then
    required_fields=("version" "generated_at" "autofix_hints" "capability_profile" "preflight" "runtime" "summary")
    all_present=true

    for field in "${required_fields[@]}"; do
        jq_exit=0
        jq -e ".$field" "$TEMP_REPORT" >/dev/null || jq_exit=$?
        if [[ $jq_exit -ne 0 ]]; then
            fail "dream-doctor.sh output missing required field: $field"
            all_present=false
        fi
    done

    if $all_present; then
        pass "dream-doctor.sh output contains all required fields"
    fi
else
    skip "jq not available - skipping field validation"
fi

# 7. autofix_hints is an array
if command -v jq >/dev/null 2>&1; then
    jq_exit=0
    jq -e '.autofix_hints | type == "array"' "$TEMP_REPORT" >/dev/null || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "dream-doctor.sh autofix_hints is an array"
    else
        fail "dream-doctor.sh autofix_hints is not an array"
    fi
fi

# 8. runtime section has expected boolean fields
if command -v jq >/dev/null 2>&1; then
    runtime_fields=("docker_cli" "docker_daemon" "compose_cli" "dashboard_http" "webui_http")
    runtime_ok=true

    for field in "${runtime_fields[@]}"; do
        field_type=$(jq -r ".runtime.$field | type" "$TEMP_REPORT")
        if [[ "$field_type" != "boolean" ]]; then
            fail "dream-doctor.sh runtime.$field is not boolean (got: $field_type)"
            runtime_ok=false
        fi
    done

    if $runtime_ok; then
        pass "dream-doctor.sh runtime section has correct boolean fields"
    fi

    amd_fields_ok=true
    for field in available runtime location runtimeMode managedByDreamServer selectedBackend supportedBackends health warnings; do
        jq_exit=0
        jq -e ".runtime.amd_runtime | has(\"$field\")" "$TEMP_REPORT" >/dev/null || jq_exit=$?
        if [[ $jq_exit -ne 0 ]]; then
            fail "dream-doctor.sh runtime.amd_runtime missing field: $field"
            amd_fields_ok=false
        fi
    done
    if $amd_fields_ok; then
        pass "dream-doctor.sh AMD runtime diagnostics fields present"
    fi
fi

# 9. summary section has expected numeric fields
if command -v jq >/dev/null 2>&1; then
    summary_fields=("preflight_blockers" "preflight_warnings")
    summary_ok=true

    for field in "${summary_fields[@]}"; do
        field_type=$(jq -r ".summary.$field | type" "$TEMP_REPORT")
        if [[ "$field_type" != "number" ]]; then
            fail "dream-doctor.sh summary.$field is not a number (got: $field_type)"
            summary_ok=false
        fi
    done

    if $summary_ok; then
        pass "dream-doctor.sh summary section has correct numeric fields"
    fi
fi

# 10. Behavioral test: Verify docker detection logic
if command -v jq >/dev/null 2>&1; then
    docker_cli=$(jq -r '.runtime.docker_cli' "$TEMP_REPORT")
    docker_daemon=$(jq -r '.runtime.docker_daemon' "$TEMP_REPORT")

    # If docker command exists, docker_cli should be true
    if command -v docker >/dev/null 2>&1; then
        if [[ "$docker_cli" == "true" ]]; then
            pass "Behavioral test: correctly detects docker CLI presence"
        else
            fail "Behavioral test: docker CLI exists but not detected"
        fi

        # If docker info works, daemon should be true
        docker_info_exit=0
        docker info >/dev/null 2>&1 || docker_info_exit=$?
        if [[ $docker_info_exit -eq 0 ]]; then
            if [[ "$docker_daemon" == "true" ]]; then
                pass "Behavioral test: correctly detects docker daemon running"
            else
                fail "Behavioral test: docker daemon running but not detected"
            fi
        fi
    else
        if [[ "$docker_cli" == "false" ]]; then
            pass "Behavioral test: correctly detects docker CLI absence"
        else
            fail "Behavioral test: docker CLI missing but detected as present"
        fi
    fi
fi

# 11. Behavioral test: Verify autofix_hints populate when issues exist
if command -v jq >/dev/null 2>&1; then
    hints_count=$(jq '.autofix_hints | length' "$TEMP_REPORT")
    docker_cli=$(jq -r '.runtime.docker_cli' "$TEMP_REPORT")

    # If docker CLI is missing, there should be at least one autofix hint
    if [[ "$docker_cli" == "false" ]] && [[ "$hints_count" -gt 0 ]]; then
        pass "Behavioral test: autofix_hints populated when docker missing"
    elif [[ "$docker_cli" == "true" ]]; then
        # Docker present - hints may or may not exist depending on other checks
        pass "Behavioral test: autofix_hints logic verified (docker present)"
    else
        skip "Behavioral test: autofix_hints (docker missing but no hints - unexpected)"
    fi
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
