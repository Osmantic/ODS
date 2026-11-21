#!/usr/bin/env bash
# ============================================================================
# ODS Doctor model-router local route diagnosis tests
# ============================================================================
# Verifies that when ODS_MODE=cloud, routes pointing to the local model-router
# (LLM_API_URL or HERMES_LLM_BASE_URL on port 9099) are diagnosed as local
# routes that conflict with cloud mode, while remote domains containing
# model-router are not falsely diagnosed.
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
SKIPPED=0

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}  $1"; SKIPPED=$((SKIPPED + 1)); }

echo ""
echo "ODS Doctor Model-Router Route Diagnosis Tests"
echo "=============================================="

command -v jq >/dev/null 2>&1 || {
    skip "jq unavailable"
    echo ""
    echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
    exit 0
}

TMP_DIR="$(mktemp -d)"
REPORT="$TMP_DIR/doctor.json"
ENV_PATH="$ROOT_DIR/.env"
FLAGS_PATH="$ROOT_DIR/.compose-flags"

HAD_ENV=false
HAD_FLAGS=false
ENV_BACKUP="$TMP_DIR/env.backup"
FLAGS_BACKUP="$TMP_DIR/compose-flags.backup"

cleanup() {
    if [[ "$HAD_ENV" == "true" ]]; then
        cp "$ENV_BACKUP" "$ENV_PATH"
    else
        rm -f "$ENV_PATH"
    fi
    if [[ "$HAD_FLAGS" == "true" ]]; then
        cp "$FLAGS_BACKUP" "$FLAGS_PATH"
    else
        rm -f "$FLAGS_PATH"
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

[[ -f "$ENV_PATH" ]] && HAD_ENV=true && cp "$ENV_PATH" "$ENV_BACKUP"
[[ -f "$FLAGS_PATH" ]] && HAD_FLAGS=true && cp "$FLAGS_PATH" "$FLAGS_BACKUP"

run_doctor() {
    rm -f "$REPORT"
    if ! (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$REPORT" >"$TMP_DIR/doctor.log" 2>&1); then
        cat "$TMP_DIR/doctor.log"
        fail "doctor command failed"
        exit 1
    fi
    if ! jq -e '.diagnoses | type == "array"' "$REPORT" >/dev/null; then
        fail "doctor did not produce a valid diagnosis report"
        exit 1
    fi
}

# Test 1: In-stack model-router is diagnosed as a local route in cloud mode
cat > "$ENV_PATH" <<'ENV'
ODS_MODE=cloud
LLM_API_URL=http://model-router:9099
HERMES_LLM_BASE_URL=http://model-router:9099/v1
ENV
cat > "$FLAGS_PATH" <<'FLAGS'
-f docker-compose.base.yml -f docker-compose.cloud.yml
FLAGS

run_doctor

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE")' "$REPORT" >/dev/null; then
    pass "diagnosis present: ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE"
else
    fail "diagnosis missing: ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE"
fi

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE")' "$REPORT" >/dev/null; then
    pass "diagnosis present: ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE"
else
    fail "diagnosis missing: ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE"
fi

# Test 2: Remote domain containing 'model-router' is not diagnosed as local
cat > "$ENV_PATH" <<'ENV'
ODS_MODE=cloud
LLM_API_URL=https://api.model-router.com/v1
HERMES_LLM_BASE_URL=https://api.model-router.com/v1
ENV

run_doctor

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE")' "$REPORT" >/dev/null; then
    fail "diagnosis wrongly triggered for remote domain: ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE"
else
    pass "remote domain spared from false local route diagnosis"
fi

# Test 3: Cloud config with switchboard enabled vs disabled does not misfire
cat > "$ENV_PATH" <<'ENV'
ODS_MODE=cloud
ODS_MODEL_SWITCHBOARD=enabled
LLM_API_URL=http://litellm:4000
HERMES_LLM_BASE_URL=http://litellm:4000/v1
LITELLM_KEY=sk-test-key
ENV

run_doctor

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE" or .id == "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE")' "$REPORT" >/dev/null; then
    fail "switchboard cloud route misfired local route diagnoses"
else
    pass "switchboard cloud route passes with zero local route misfires"
fi

# A provider may route through a path containing another endpoint. Those
# path/query strings must not be interpreted as the selected destination.
for remote_url in \
    'https://provider.example/v1/localhost:9099' \
    'https://provider.example//model-router:9099/v1' \
    'https://provider.example/v1?upstream=http://llama-server:8080' \
    'https://api.llama-server.example/v1'; do
    printf 'ODS_MODE=cloud\nLLM_API_URL=%s\nHERMES_LLM_BASE_URL=%s\n' \
        "$remote_url" "$remote_url" > "$ENV_PATH"
    run_doctor
    if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE" or .id == "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE")' "$REPORT" >/dev/null; then
        fail "remote URL path, query, or hostname wrongly diagnosed: $remote_url"
    else
        pass "remote URL authority respected: $remote_url"
    fi
done

for local_url in 'http://model-router/v1' 'http://ODS-MODEL-ROUTER:9099/v1' 'http://[::1]:9099/v1'; do
    printf 'ODS_MODE=cloud\nLLM_API_URL=%s\nHERMES_LLM_BASE_URL=%s\n' \
        "$local_url" "$local_url" > "$ENV_PATH"
    run_doctor
    if jq -e '[.diagnoses[] | select(.id == "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE" or .id == "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE")] | length == 2' "$REPORT" >/dev/null; then
        pass "local runtime destination diagnosed: $local_url"
    else
        fail "local runtime destination missed: $local_url"
    fi
done

echo ""
echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[[ "$FAILED" -eq 0 ]] || exit 1
