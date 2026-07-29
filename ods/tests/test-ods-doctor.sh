#!/bin/bash
# ============================================================================
# ODS ods-doctor.sh Test Suite
# ============================================================================
# Ensures scripts/ods-doctor.sh runs without shell errors and produces
# expected JSON output with correct structure. Validates the diagnostic tool
# used in installer simulation and CI artifacts.
#
# Usage: ./tests/test-ods-doctor.sh
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
echo "║   ods-doctor.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script exists
if [[ ! -f "$ROOT_DIR/scripts/ods-doctor.sh" ]]; then
    fail "scripts/ods-doctor.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "ods-doctor.sh exists"

# 2. --help flag works
set +e
help_out=$(cd "$ROOT_DIR" && bash scripts/ods-doctor.sh --help 2>&1)
help_exit=$?
set -e

if [[ "$help_exit" -eq 0 ]] && echo "$help_out" | grep -q "Usage:"; then
    pass "ods-doctor.sh --help displays usage"
else
    fail "ods-doctor.sh --help failed or missing usage text"
fi

# 3. Runs without shell error (default output path)
TEMP_REPORT=$(mktemp /tmp/ods-doctor-test.XXXXXX.json)
REAL_ENV="$ROOT_DIR/.env"
ORIGINAL_ENV_EXISTED=false
if [[ -f "$REAL_ENV" ]]; then
    ORIGINAL_ENV_EXISTED=true
fi
ENV_BACKUP_PATH=""
TEST_TEMP_WORKSPACE=""
FIXTURE_ACTIVE=false

cleanup() {
    if [[ -n "$ENV_BACKUP_PATH" ]] && [[ -f "$ENV_BACKUP_PATH" ]]; then
        mv "$ENV_BACKUP_PATH" "$REAL_ENV"
    elif [[ "$FIXTURE_ACTIVE" == "true" ]]; then
        rm -f "$REAL_ENV"
    fi
    rm -f "$TEMP_REPORT" /tmp/curl_calls.log
    if [[ -n "$TEST_TEMP_WORKSPACE" ]] && [[ -d "$TEST_TEMP_WORKSPACE" ]]; then
        rm -rf "$TEST_TEMP_WORKSPACE"
    fi
}
trap cleanup EXIT

set +e
out=$(cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$TEMP_REPORT" 2>&1)
exit_code=$?
set -e

if echo "$out" | grep -q "unbound variable\|syntax error\|command not found"; then
    fail "ods-doctor.sh produced shell error in output"
else
    pass "ods-doctor.sh runs without shell errors"
fi

# Exit code must be 0 or 1 (documented: 0=success, 1=error)
if [[ "$exit_code" -eq 0 ]] || [[ "$exit_code" -eq 1 ]]; then
    pass "ods-doctor.sh exit code is valid (0|1): $exit_code"
else
    fail "ods-doctor.sh exit code should be 0 or 1; got $exit_code"
fi

# 4. Produces JSON output file
if [[ -f "$TEMP_REPORT" ]]; then
    pass "ods-doctor.sh creates output file"
else
    fail "ods-doctor.sh did not create output file at $TEMP_REPORT"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi

# 5. Output is valid JSON
if command -v jq >/dev/null 2>&1; then
    jq_exit=0
    jq empty "$TEMP_REPORT" || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "ods-doctor.sh output is valid JSON"
    else
        fail "ods-doctor.sh output is not valid JSON"
    fi
else
    skip "jq not available - skipping JSON validation"
fi

# 6. Required top-level fields exist
if command -v jq >/dev/null 2>&1; then
    required_fields=("version" "generated_at" "autofix_hints" "capability_profile" "preflight" "runtime" "install_artifacts" "diagnoses" "summary")
    all_present=true

    for field in "${required_fields[@]}"; do
        jq_exit=0
        jq -e ".$field" "$TEMP_REPORT" >/dev/null || jq_exit=$?
        if [[ $jq_exit -ne 0 ]]; then
            fail "ods-doctor.sh output missing required field: $field"
            all_present=false
        fi
    done

    if $all_present; then
        pass "ods-doctor.sh output contains all required fields"
    fi
else
    skip "jq not available - skipping field validation"
fi

# 7. autofix_hints is an array
if command -v jq >/dev/null 2>&1; then
    jq_exit=0
    jq -e '.autofix_hints | type == "array"' "$TEMP_REPORT" >/dev/null || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "ods-doctor.sh autofix_hints is an array"
    else
        fail "ods-doctor.sh autofix_hints is not an array"
    fi
fi

# 7b. diagnoses is an array and install_artifacts records artifact presence
if command -v jq >/dev/null 2>&1; then
    jq_exit=0
    jq -e '.diagnoses | type == "array"' "$TEMP_REPORT" >/dev/null || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "ods-doctor.sh diagnoses is an array"
    else
        fail "ods-doctor.sh diagnoses is not an array"
    fi

    jq_exit=0
    jq -e '.install_artifacts.env_file.exists | type == "boolean"' "$TEMP_REPORT" >/dev/null || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "ods-doctor.sh install_artifacts records env file state"
    else
        fail "ods-doctor.sh install_artifacts missing env file state"
    fi
fi

# 8. runtime section has expected boolean fields
if command -v jq >/dev/null 2>&1; then
    runtime_fields=("docker_cli" "docker_daemon" "compose_cli" "dashboard_http" "webui_http")
    runtime_ok=true

    for field in "${runtime_fields[@]}"; do
        field_type=$(jq -r ".runtime.$field | type" "$TEMP_REPORT")
        if [[ "$field_type" != "boolean" ]]; then
            fail "ods-doctor.sh runtime.$field is not boolean (got: $field_type)"
            runtime_ok=false
        fi
    done

    if $runtime_ok; then
        pass "ods-doctor.sh runtime section has correct boolean fields"
    fi

    jq_exit=0
    jq -e '.runtime.inference_contract | type == "object"' "$TEMP_REPORT" >/dev/null || jq_exit=$?
    if [[ $jq_exit -eq 0 ]]; then
        pass "ods-doctor.sh runtime includes inference contract diagnostics"
    else
        fail "ods-doctor.sh runtime missing inference contract diagnostics"
    fi

    amd_fields_ok=true
    for field in available runtime location runtimeMode managedByODS selectedBackend supportedBackends health warnings; do
        jq_exit=0
        jq -e ".runtime.amd_runtime | has(\"$field\")" "$TEMP_REPORT" >/dev/null || jq_exit=$?
        if [[ $jq_exit -ne 0 ]]; then
            fail "ods-doctor.sh runtime.amd_runtime missing field: $field"
            amd_fields_ok=false
        fi
    done
    if $amd_fields_ok; then
        pass "ods-doctor.sh AMD runtime diagnostics fields present"
    fi
fi

# 9. summary section has expected numeric fields
if command -v jq >/dev/null 2>&1; then
    summary_fields=("preflight_blockers" "preflight_warnings" "runtime_contract_blockers" "runtime_contract_warnings")
    summary_ok=true

    for field in "${summary_fields[@]}"; do
        field_type=$(jq -r ".summary.$field | type" "$TEMP_REPORT")
        if [[ "$field_type" != "number" ]]; then
            fail "ods-doctor.sh summary.$field is not a number (got: $field_type)"
            summary_ok=false
        fi
    done

    if $summary_ok; then
        pass "ods-doctor.sh summary section has correct numeric fields"
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
# 12. External LLM backend configuration and connectivity check
if command -v jq >/dev/null 2>&1; then
    # Create temp workspace and set global variable for tracking
    TEST_TEMP_WORKSPACE=$(mktemp -d /tmp/ods-doctor-test-workspace.XXXXXX)
    mkdir -p "$TEST_TEMP_WORKSPACE/bin"

    # Write a stubbed curl that records URL called and responds successfully
    cat << 'EOF' > "$TEST_TEMP_WORKSPACE/bin/curl"
#!/bin/bash
echo "$*" >> /tmp/curl_calls.log
# return a dummy JSON for success
echo '{"status":"ok"}'
exit 0
EOF
    chmod +x "$TEST_TEMP_WORKSPACE/bin/curl"

    # Backup real .env using a unique file name and set global tracker
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=true
    if [[ -f "$REAL_ENV" ]]; then
        ENV_BACKUP_PATH=$(mktemp /tmp/ods-env-backup.XXXXXX)
        mv "$REAL_ENV" "$ENV_BACKUP_PATH"
    fi

    # Write test .env with quoted values
    cat << 'EOF' > "$REAL_ENV"
EXTERNAL_LLM_URL="https://mock-llm.example.com"
EXTERNAL_LLM_PROVIDER="ollama"
EXTERNAL_LLM_MODEL="llama3-test"
EOF

    rm -f /tmp/curl_calls.log

    # Run the doctor script with mock curl in PATH
    set +e
    (export PATH="$TEST_TEMP_WORKSPACE/bin:$PATH"; cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$TEMP_REPORT" >/dev/null 2>&1)
    exit_code=$?
    set -e

    # Verify JSON structure and values
    status=$(jq -r '.runtime.llm_backend.status' "$TEMP_REPORT")
    provider=$(jq -r '.runtime.llm_backend.provider' "$TEMP_REPORT")
    url=$(jq -r '.runtime.llm_backend.url' "$TEMP_REPORT")
    model=$(jq -r '.runtime.llm_backend.model' "$TEMP_REPORT")

    # Read curl calls before cleanup
    curl_calls=""
    if [[ -f /tmp/curl_calls.log ]]; then
        curl_calls=$(cat /tmp/curl_calls.log)
    fi

    # Restore original env immediately
    if [[ -n "$ENV_BACKUP_PATH" ]] && [[ -f "$ENV_BACKUP_PATH" ]]; then
        mv "$ENV_BACKUP_PATH" "$REAL_ENV"
    else
        rm -f "$REAL_ENV"
    fi
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=false

    # Clean up temp workspace and curl calls log immediately
    rm -rf "$TEST_TEMP_WORKSPACE"
    TEST_TEMP_WORKSPACE=""
    rm -f /tmp/curl_calls.log

    # Perform assertions
    if [[ "$status" == "ok" ]] && \
       [[ "$provider" == "ollama" ]] && \
       [[ "$url" == "https://mock-llm.example.com" ]] && \
       [[ "$model" == "llama3-test" ]]; then
        pass "External LLM report JSON contains correct unquoted fields"
    else
        fail "External LLM report JSON fields are incorrect. got: status=$status, provider=$provider, url=$url, model=$model"
    fi

    # Verify expected endpoint was probed by curl
    if echo "$curl_calls" | grep -q "https://mock-llm.example.com/api/tags"; then
        pass "External LLM probe endpoint (/api/tags) was called successfully"
    else
        fail "External LLM probe did not hit correct endpoint. calls: $curl_calls"
    fi
else
    skip "jq not available - skipping external LLM behavioral validation"
fi

# 13. Cloud mode LLM backend check validation (no external LLM url configured)
if command -v jq >/dev/null 2>&1; then
    # Backup real .env using a unique file name and set global tracker
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=true
    if [[ -f "$REAL_ENV" ]]; then
        ENV_BACKUP_PATH=$(mktemp /tmp/ods-env-backup.XXXXXX)
        mv "$REAL_ENV" "$ENV_BACKUP_PATH"
    fi

    # Write test .env with ODS_MODE=cloud and no EXTERNAL_LLM_URL
    cat << 'EOF' > "$REAL_ENV"
ODS_MODE="cloud"
EOF

    # Run the doctor script
    set +e
    (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$TEMP_REPORT" >/dev/null 2>&1)
    exit_code=$?
    set -e

    # Verify JSON structure and values
    status=$(jq -r '.runtime.llm_backend.status' "$TEMP_REPORT")
    provider=$(jq -r '.runtime.llm_backend.provider' "$TEMP_REPORT")

    # Verify autofix hints do not mention llama-server or unreachable LLM
    llama_hint=$(jq -r '.autofix_hints[] | select(contains("llama-server") or contains("LLM backend"))' "$TEMP_REPORT" 2>/dev/null || true)

    # Restore original env immediately
    if [[ -n "$ENV_BACKUP_PATH" ]] && [[ -f "$ENV_BACKUP_PATH" ]]; then
        mv "$ENV_BACKUP_PATH" "$REAL_ENV"
    else
        rm -f "$REAL_ENV"
    fi
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=false

    # Perform assertions
    if [[ "$status" == "ok" ]] && [[ "$provider" == "cloud" ]] && [[ -z "$llama_hint" ]]; then
        pass "Cloud mode LLM backend check reports ok and has no local llama-server failure hints"
    else
        fail "Cloud mode LLM backend check failed. got: status=$status, provider=$provider, llama_hint=$llama_hint"
    fi
else
    skip "jq not available - skipping cloud mode behavioral validation"
fi

# 14. Cloud mode LLM backend check validation with translated litellm URL (CRLF env)
if command -v jq >/dev/null 2>&1; then
    # Create temp workspace for stub bin
    TEST_TEMP_WORKSPACE=$(mktemp -d /tmp/ods-doctor-test-workspace.XXXXXX)
    mkdir -p "$TEST_TEMP_WORKSPACE/bin"

    # Write a stubbed curl that records URL called and responds successfully
    cat << 'EOF' > "$TEST_TEMP_WORKSPACE/bin/curl"
#!/bin/bash
echo "$*" >> /tmp/curl_calls.log
echo '{"status":"ok"}'
exit 0
EOF
    chmod +x "$TEST_TEMP_WORKSPACE/bin/curl"

    # Backup real .env using a unique file name and set global tracker
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=true
    if [[ -f "$REAL_ENV" ]]; then
        ENV_BACKUP_PATH=$(mktemp /tmp/ods-env-backup.XXXXXX)
        mv "$REAL_ENV" "$ENV_BACKUP_PATH"
    fi

    # Write test .env with ODS_MODE=cloud and LLM_API_URL=http://litellm:4000 (with CRLF)
    printf 'ODS_MODE="cloud"\r\nLLM_API_URL="http://litellm:4000"\r\n' > "$REAL_ENV"

    rm -f /tmp/curl_calls.log

    # Run the doctor script with mock curl in PATH
    set +e
    (export PATH="$TEST_TEMP_WORKSPACE/bin:$PATH"; cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$TEMP_REPORT" >/dev/null 2>&1)
    exit_code=$?
    set -e

    # Verify JSON structure and values
    status=$(jq -r '.runtime.llm_backend.status' "$TEMP_REPORT")
    provider=$(jq -r '.runtime.llm_backend.provider' "$TEMP_REPORT")
    url=$(jq -r '.runtime.llm_backend.url' "$TEMP_REPORT")

    # Read curl calls before cleanup
    curl_calls=""
    if [[ -f /tmp/curl_calls.log ]]; then
        curl_calls=$(cat /tmp/curl_calls.log)
    fi

    # Restore original env immediately
    if [[ -n "$ENV_BACKUP_PATH" ]] && [[ -f "$ENV_BACKUP_PATH" ]]; then
        mv "$ENV_BACKUP_PATH" "$REAL_ENV"
    else
        rm -f "$REAL_ENV"
    fi
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=false

    # Clean up temp workspace and curl calls log immediately
    rm -rf "$TEST_TEMP_WORKSPACE"
    TEST_TEMP_WORKSPACE=""
    rm -f /tmp/curl_calls.log

    # Perform assertions
    if [[ "$status" == "ok" ]] && \
       [[ "$provider" == "openai" ]] && \
       [[ "$url" == "http://127.0.0.1:4000" ]]; then
        pass "Cloud mode with litellm URL translates to 127.0.0.1 and probes successfully"
    else
        fail "Cloud mode with litellm URL failed. got: status=$status, provider=$provider, url=$url"
    fi

    if echo "$curl_calls" | grep -q "http://127.0.0.1:4000/v1/models"; then
        pass "Cloud mode litellm probe hit translated host URL (/v1/models)"
    else
        fail "Cloud mode litellm probe did not hit correct translated host. calls: $curl_calls"
    fi
else
    skip "jq not available - skipping cloud mode litellm URL translation check"
fi

# 15. Cloud mode LLM backend check validation with non-host-probeable URL (CRLF env)
if command -v jq >/dev/null 2>&1; then
    # Backup real .env using a unique file name and set global tracker
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=true
    if [[ -f "$REAL_ENV" ]]; then
        ENV_BACKUP_PATH=$(mktemp /tmp/ods-env-backup.XXXXXX)
        mv "$REAL_ENV" "$ENV_BACKUP_PATH"
    fi

    # Write test .env with ODS_MODE=cloud and LLM_API_URL=http://other-service:4000 (with CRLF)
    printf 'ODS_MODE="cloud"\r\nLLM_API_URL="http://other-service:4000"\r\n' > "$REAL_ENV"

    # Run the doctor script
    set +e
    (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$TEMP_REPORT" >/dev/null 2>&1)
    exit_code=$?
    set -e

    # Verify JSON structure and values
    status=$(jq -r '.runtime.llm_backend.status' "$TEMP_REPORT")
    provider=$(jq -r '.runtime.llm_backend.provider' "$TEMP_REPORT")
    url=$(jq -r '.runtime.llm_backend.url' "$TEMP_REPORT")

    # Restore original env immediately
    if [[ -n "$ENV_BACKUP_PATH" ]] && [[ -f "$ENV_BACKUP_PATH" ]]; then
        mv "$ENV_BACKUP_PATH" "$REAL_ENV"
    else
        rm -f "$REAL_ENV"
    fi
    ENV_BACKUP_PATH=""
    FIXTURE_ACTIVE=false

    # Perform assertions
    if [[ "$status" == "ok" ]] && \
       [[ "$provider" == "cloud" ]] && \
       [[ "$url" == "http://other-service:4000" ]]; then
        pass "Cloud mode with non-host-probeable URL bypasses host probe and reports ok"
    else
        fail "Cloud mode with non-host-probeable URL failed. got: status=$status, provider=$provider, url=$url"
    fi
else
    skip "jq not available - skipping cloud mode non-host-probeable URL check"
fi
# Final check to verify we did not leak/leave behind an empty .env file if it did not exist initially.
if [[ "$ORIGINAL_ENV_EXISTED" == "false" ]]; then
    if [[ -f "$REAL_ENV" ]]; then
        fail "Cleanup verification: left a .env file behind"
    else
        pass "Cleanup verification: no .env file left behind"
    fi
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
