#!/usr/bin/env bash
# Phase 11 may recreate only compose-owned services that are already exited.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE11="$ROOT_DIR/installers/phases/11-services.sh"
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

eval "$(extract_phase11_function _phase11_recreate_exited_services)"
declare -F _phase11_recreate_exited_services >/dev/null \
    || fail "could not extract _phase11_recreate_exited_services"

MOCK_DOCKER="$TMP_DIR/docker"
CALL_LOG="$TMP_DIR/docker.calls"
LOG_FILE="$TMP_DIR/install.log"
cat > "$MOCK_DOCKER" <<'MOCK'
#!/usr/bin/env bash
set -u

printf '%s\n' "$*" >> "$MOCK_DOCKER_CALL_LOG"
case " $* " in
    *" ps --status exited --services "*)
        printf '%b' "${MOCK_EXITED_SERVICES:-}"
        ;;
    *" up -d --no-deps --force-recreate --no-build --pull never "*)
        exit "${MOCK_RECREATE_EXIT_CODE:-0}"
        ;;
esac
MOCK
chmod +x "$MOCK_DOCKER"

export MOCK_DOCKER_CALL_LOG="$CALL_LOG"
export DOCKER_COMPOSE_CMD="$MOCK_DOCKER compose"
export LOG_FILE
COMPOSE_FLAGS_ARR=(-f docker-compose.base.yml)
ai_warn() { printf 'WARN: %s\n' "$*" >> "$LOG_FILE"; }
log() { printf 'LOG: %s\n' "$*" >> "$LOG_FILE"; }

export MOCK_EXITED_SERVICES=$'perplexica\nperplexica\nn8n\n'
_phase11_recreate_exited_services \
    || fail "valid exited compose services were not recreated"
grep -qF 'up -d --no-deps --force-recreate --no-build --pull never perplexica n8n' "$CALL_LOG" \
    || fail "recovery did not deduplicate and recreate only the exited services"
grep -qF 'WARN: Recreating exited service container(s) with stale runtime state: perplexica n8n' "$LOG_FILE" \
    || fail "operator did not receive the bounded recovery notice"
pass "exited compose services receive one bounded force-recreate"

: > "$CALL_LOG"
: > "$LOG_FILE"
export MOCK_EXITED_SERVICES=$'perplexica\nbad/service\n'
if _phase11_recreate_exited_services; then
    fail "malformed compose service name was accepted"
fi
! grep -q ' up -d ' "$CALL_LOG" \
    || fail "recovery executed after a malformed compose service name"
grep -qF 'Refusing malformed exited compose service name' "$LOG_FILE" \
    || fail "malformed service rejection was not logged"
pass "malformed service evidence fails closed before recreation"

: > "$CALL_LOG"
: > "$LOG_FILE"
export MOCK_EXITED_SERVICES=''
_phase11_recreate_exited_services \
    || fail "empty exited-service inventory should be a no-op"
! grep -q ' up -d ' "$CALL_LOG" \
    || fail "empty exited-service inventory triggered recreation"
pass "healthy compose inventory remains untouched"
