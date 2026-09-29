#!/usr/bin/env bash
# Phase 11 recovery may start only containers owned by this Compose project.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE11="${ODS_PHASE11_SOURCE:-$ROOT_DIR/installers/phases/11-services.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

extract_phase11_function() {
    sed -n "/^${1}() {/,/^}$/p" "$PHASE11"
}

helper_source="$(extract_phase11_function _phase11_start_created_containers)"
if [[ -n "$helper_source" ]]; then
    eval "$helper_source"
fi

recovery_block="$(sed -n \
    '/# Step 1: start any containers already in Created state/,/# Step 3: catch any stragglers from the second pass/p' \
    "$PHASE11")"
[[ -n "$recovery_block" ]] || fail "could not extract the Phase 11 recovery block"

MOCK_DOCKER="$TMP_DIR/docker"
CALL_LOG="$TMP_DIR/docker.calls"
START_LOG="$TMP_DIR/docker.starts"
LOG_FILE="$TMP_DIR/install.log"
cat > "$MOCK_DOCKER" <<'MOCK'
#!/usr/bin/env bash
set -u

printf '%s\n' "$*" >> "$MOCK_DOCKER_CALL_LOG"
case " $* " in
    *" ps -a --filter status=created -q "*)
        printf '%s\n%s\n' "$MOCK_FOREIGN_CREATED_ID" "$MOCK_ODS_CREATED_ID"
        ;;
    *" ps --all --orphans=false --status created --quiet "*)
        [[ "${MOCK_COMPOSE_PS_FAIL:-false}" == true ]] && exit 1
        [[ -n "${MOCK_ODS_CREATED_ID:-}" ]] && printf '%s\n' "$MOCK_ODS_CREATED_ID"
        ;;
    *" start "*)
        printf '%s\n' "$*" >> "$MOCK_DOCKER_START_LOG"
        ;;
    *" up -d --remove-orphans --no-build --pull never "*)
        exit "${MOCK_COMPOSE_UP_EXIT_CODE:-0}"
        ;;
esac
MOCK
chmod +x "$MOCK_DOCKER"

export MOCK_DOCKER_CALL_LOG="$CALL_LOG"
export MOCK_DOCKER_START_LOG="$START_LOG"
export MOCK_FOREIGN_CREATED_ID="f111111111111111"
export MOCK_ODS_CREATED_ID="a222222222222222"
export DOCKER_CMD="$MOCK_DOCKER"
export DOCKER_COMPOSE_CMD="$MOCK_DOCKER compose"
export LOG_FILE
COMPOSE_FLAGS_ARR=(-f docker-compose.base.yml)
compose_ok=false
ai_warn() { printf 'WARN: %s\n' "$*" >> "$LOG_FILE"; }
log() { printf 'LOG: %s\n' "$*" >> "$LOG_FILE"; }
sleep() { :; }

run_recovery_block() {
    eval "$recovery_block"
}

run_recovery_block || fail "Phase 11 recovery block failed"
! grep -qF "$MOCK_FOREIGN_CREATED_ID" "$CALL_LOG" \
    || fail "recovery started a created container from another Docker project"
grep -qF "start $MOCK_ODS_CREATED_ID" "$CALL_LOG" \
    || fail "recovery did not start the created container in the current Compose project"
grep -qFx "start $MOCK_ODS_CREATED_ID" "$START_LOG" \
    || fail "mock Docker did not successfully start the current project's created container"
! grep -qF "$MOCK_FOREIGN_CREATED_ID" "$START_LOG" \
    || fail "recovery started a created container from another Docker project"
! grep -qF 'ps -a --filter status=created' "$CALL_LOG" \
    || fail "recovery enumerated Created containers host-wide"
grep -qF 'ps --all --orphans=false --status created --quiet' "$CALL_LOG" \
    || fail "recovery did not query the current Compose project"
pass "Phase 11 starts created containers from the current Compose project only"

: > "$CALL_LOG"
export MOCK_ODS_CREATED_ID=""
run_recovery_block || fail "empty project recovery block failed"
! grep -qE '^start ' "$CALL_LOG" \
    || fail "recovery started a foreign container when this project had no created containers"
pass "foreign created containers remain untouched when the ODS project has none"

: > "$CALL_LOG"
export MOCK_ODS_CREATED_ID="a222222222222222"
export MOCK_COMPOSE_PS_FAIL=true
run_recovery_block || fail "failed Compose query aborted the best-effort recovery block"
! grep -qE '^start ' "$CALL_LOG" \
    || fail "recovery started host-wide containers after its project query failed"
pass "failed project enumeration does not fall back to a host-wide start"
