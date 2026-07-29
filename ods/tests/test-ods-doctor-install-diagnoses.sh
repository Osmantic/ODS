#!/usr/bin/env bash
# ============================================================================
# ODS Doctor install diagnosis tests
# ============================================================================
# Exercises evidence-ranked diagnoses with local fixture artifacts. The test
# avoids Docker and network assumptions; it only verifies that doctor can turn
# saved installer evidence into stable root-cause IDs and remediation hints.
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
echo "ODS Doctor Install Diagnosis Tests"
echo "===================================="

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
LOGS_DIR="$ROOT_DIR/logs"
LAUNCH_PATH="$LOGS_DIR/compose-launch.txt"
COMPOSE_UP_PATH="$LOGS_DIR/compose-up.log"
INSTALL_LOG_PATH="$LOGS_DIR/install.log"
INSTALL_REPORT_PATH="$ROOT_DIR/install-report-2999-01-01-000000.txt"

HAD_ENV=false
HAD_FLAGS=false
HAD_LAUNCH=false
HAD_COMPOSE_UP=false
HAD_INSTALL_LOG=false
HAD_LOGS_DIR=false
ENV_BACKUP="$TMP_DIR/env.backup"
FLAGS_BACKUP="$TMP_DIR/compose-flags.backup"
LAUNCH_BACKUP="$TMP_DIR/compose-launch.backup"
COMPOSE_UP_BACKUP="$TMP_DIR/compose-up.backup"
INSTALL_LOG_BACKUP="$TMP_DIR/install-log.backup"

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
    if [[ "$HAD_LAUNCH" == "true" ]]; then
        mkdir -p "$LOGS_DIR"
        cp "$LAUNCH_BACKUP" "$LAUNCH_PATH"
    else
        rm -f "$LAUNCH_PATH"
    fi
    if [[ "$HAD_COMPOSE_UP" == "true" ]]; then
        mkdir -p "$LOGS_DIR"
        cp "$COMPOSE_UP_BACKUP" "$COMPOSE_UP_PATH"
    else
        rm -f "$COMPOSE_UP_PATH"
    fi
    if [[ "$HAD_INSTALL_LOG" == "true" ]]; then
        mkdir -p "$LOGS_DIR"
        cp "$INSTALL_LOG_BACKUP" "$INSTALL_LOG_PATH"
    else
        rm -f "$INSTALL_LOG_PATH"
    fi
    rm -f "$INSTALL_REPORT_PATH"
    if [[ "$HAD_LOGS_DIR" != "true" && -d "$LOGS_DIR" ]]; then
        rmdir "$LOGS_DIR" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

[[ -d "$LOGS_DIR" ]] && HAD_LOGS_DIR=true
[[ -f "$ENV_PATH" ]] && HAD_ENV=true && cp "$ENV_PATH" "$ENV_BACKUP"
[[ -f "$FLAGS_PATH" ]] && HAD_FLAGS=true && cp "$FLAGS_PATH" "$FLAGS_BACKUP"
[[ -f "$LAUNCH_PATH" ]] && HAD_LAUNCH=true && cp "$LAUNCH_PATH" "$LAUNCH_BACKUP"
[[ -f "$COMPOSE_UP_PATH" ]] && HAD_COMPOSE_UP=true && cp "$COMPOSE_UP_PATH" "$COMPOSE_UP_BACKUP"
[[ -f "$INSTALL_LOG_PATH" ]] && HAD_INSTALL_LOG=true && cp "$INSTALL_LOG_PATH" "$INSTALL_LOG_BACKUP"

mkdir -p "$LOGS_DIR"
cat > "$ENV_PATH" <<'ENV'
ODS_MODE=cloud
GPU_BACKEND=nvidia
LLM_API_URL=http://llama-server:8080
HERMES_LLM_BASE_URL=http://llama-server:8080/v1
LLM_MODEL=qwen-test
GGUF_FILE=qwen-test.gguf
ENV
cat > "$FLAGS_PATH" <<'FLAGS'
-f docker-compose.base.yml -f docker-compose.nvidia.yml
FLAGS
cat > "$LAUNCH_PATH" <<'LAUNCH'
timestamp=2999-01-01T00:00:00Z
cwd=/tmp/not-the-ods-install-dir
compose_command=docker compose -f docker-compose.base.yml up -d --remove-orphans --no-build --pull never
LAUNCH
cat > "$COMPOSE_UP_PATH" <<'LOG'
Docker Compose did not create any managed containers.
ModuleNotFoundError: No module named 'yaml'
LOG
cat > "$INSTALL_LOG_PATH" <<'LOG'
[XX] Docker could not download alpine:3.20, which is required for the file-sharing probe.
>   Start Docker Desktop, confirm it has internet access, then run:
>   docker pull alpine:3.20
>   Re-run this installer after the pull succeeds.
LOG
cat > "$INSTALL_REPORT_PATH" <<'REPORT_EOF'
ODS install failure report
Phase: install-core phase 11 docker compose up
Error response from daemon: failed to resolve reference "ghcr.io/example/missing-image:v0": ghcr.io/example/missing-image:v0: not found
REPORT_EOF

if (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$REPORT" >/dev/null 2>&1); then
    pass "ods-doctor runs with fixture install artifacts"
else
    fail "ods-doctor failed with fixture install artifacts"
fi

if jq -e '.diagnoses | type == "array" and length >= 5' "$REPORT" >/dev/null; then
    pass "doctor report includes evidence-ranked diagnoses"
else
    fail "doctor report missing expected diagnoses array"
fi

for id in \
    ODS-COMPOSE-CWD-MISMATCH \
    ODS-DOCKER-IMAGE-UNRESOLVED \
    ODS-COMPOSE-ZERO-CONTAINERS \
    ODS-PYTHON-PYYAML-MISSING \
    ODS-WINDOWS-FILE-SHARING-PROBE-IMAGE \
    ODS-RUNTIME-CLOUD-OVERLAY-MISSING \
    ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE \
    ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE
do
    if jq -e --arg id "$id" '.diagnoses[] | select(.id == $id)' "$REPORT" >/dev/null; then
        pass "diagnosis present: $id"
    else
        fail "diagnosis missing: $id"
    fi
done

if jq -e '.diagnoses[] | select(.id == "ODS-DOCKER-IMAGE-UNRESOLVED") | .evidence[0].source == "install-report-2999-01-01-000000.txt"' "$REPORT" >/dev/null; then
    pass "image diagnosis records install report evidence"
else
    fail "image diagnosis missing install report evidence"
fi

if jq -e '.diagnoses[] | select(.id == "ODS-WINDOWS-FILE-SHARING-PROBE-IMAGE") | .evidence[0].source == "logs/install.log"' "$REPORT" >/dev/null; then
    pass "Alpine probe diagnosis records installer log evidence"
else
    fail "Alpine probe diagnosis missing installer log evidence"
fi

if jq -e '.summary.diagnoses_blockers >= 7 and .summary.diagnoses_warnings >= 1' "$REPORT" >/dev/null; then
    pass "summary counts diagnosis blockers"
else
    fail "summary does not count diagnosis blockers"
fi

FAKE_BIN="$TMP_DIR/fake-bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/docker" <<'DOCKER'
#!/usr/bin/env bash
case "${1:-}" in
    info)
        exit 0
        ;;
    compose)
        if [[ "${2:-}" == "version" ]]; then
            echo "Docker Compose version v2.fake"
            exit 0
        fi
        ;;
    ps)
        if [[ "$*" == *"--format"* ]]; then
            printf '%s\n' ods-dashboard ods-webui ods-llama-server
        fi
        exit 0
        ;;
    inspect)
        echo running
        exit 0
        ;;
    logs)
        exit 0
        ;;
esac
exit 0
DOCKER
chmod +x "$FAKE_BIN/docker"
touch -t 202001010000 "$INSTALL_REPORT_PATH" "$COMPOSE_UP_PATH" "$INSTALL_LOG_PATH"
touch -t 202001010001 "$LAUNCH_PATH"

if (cd "$ROOT_DIR" && PATH="$FAKE_BIN:$PATH" bash scripts/ods-doctor.sh "$REPORT" >/dev/null 2>&1); then
    pass "ods-doctor runs with recovered stale zero-container artifact"
else
    fail "ods-doctor failed with recovered stale zero-container artifact"
fi

if jq -e '.diagnoses[] | select(.id == "ODS-COMPOSE-ZERO-CONTAINERS")' "$REPORT" >/dev/null; then
    fail "stale zero-container diagnosis still blocks a recovered install"
else
    pass "stale zero-container diagnosis is suppressed after recovered install"
fi

if jq -e '.runtime.inference_contract.issue_counts.blockers >= 3 and .summary.runtime_contract_blockers >= 3' "$REPORT" >/dev/null; then
    pass "runtime inference contract counts cloud/local mismatch blockers"
else
    fail "runtime inference contract missing cloud/local mismatch blockers"
fi

if jq -e '.autofix_hints[] | select(contains("known-good ODS version"))' "$REPORT" >/dev/null; then
    pass "diagnosis next steps feed autofix hints"
else
    fail "diagnosis next steps missing from autofix hints"
fi

cat > "$ENV_PATH" <<'ENV'
ODS_MODE=lemonade
GPU_BACKEND=amd
LLM_API_URL=http://litellm:4000
HERMES_LLM_BASE_URL=http://litellm:4000/v1
LEMONADE_EXTERNAL=true
LEMONADE_BASE_URL=http://localhost:13305
LEMONADE_CONTAINER_BASE_URL=http://host.docker.internal:13305
LITELLM_LEMONADE_API_KEY=sk-ods-lemonade-fixture
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_MANAGED=false
AMD_INFERENCE_RUNTIME_MODE=external-lemonade
ENV
cat > "$FLAGS_PATH" <<'FLAGS'
-f docker-compose.base.yml -f docker-compose.cloud.yml -f docker-compose.lemonade-external.yml
FLAGS

if (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$REPORT" >/dev/null 2>&1); then
    pass "ods-doctor runs with external Lemonade fixture"
else
    fail "ods-doctor failed with external Lemonade fixture"
fi

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE")' "$REPORT" >/dev/null; then
    pass "external Lemonade host route without user API key is diagnosed"
else
    fail "external Lemonade host route without user API key was not diagnosed"
fi

cat > "$ENV_PATH" <<'ENV'
ODS_MODE=lemonade
GPU_BACKEND=amd
LLM_API_URL=http://litellm:4000
HERMES_LLM_BASE_URL=http://litellm:4000/v1
LEMONADE_EXTERNAL=true
LEMONADE_BASE_URL=http://localhost:13305
LEMONADE_CONTAINER_BASE_URL=http://host.docker.internal:13305
LEMONADE_API_KEY=sk-user-lemonade-fixture
LITELLM_LEMONADE_API_KEY=sk-user-lemonade-fixture
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_MANAGED=false
AMD_INFERENCE_RUNTIME_MODE=external-lemonade
ENV

if (cd "$ROOT_DIR" && bash scripts/ods-doctor.sh "$REPORT" >/dev/null 2>&1); then
    pass "ods-doctor runs with authenticated external Lemonade fixture"
else
    fail "ods-doctor failed with authenticated external Lemonade fixture"
fi

if jq -e '.diagnoses[] | select(.id == "ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE")' "$REPORT" >/dev/null; then
    fail "authenticated external Lemonade fixture still emitted unauthenticated route diagnosis"
else
    pass "user API key suppresses unauthenticated external Lemonade diagnosis"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[[ "$FAILED" -eq 0 ]]
