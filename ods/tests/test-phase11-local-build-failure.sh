#!/usr/bin/env bash
# A failed requested local build must not fall back to a stale tagged image.

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

eval "$(extract_phase11_function _phase11_build_local_images)"
declare -F _phase11_build_local_images >/dev/null \
    || fail "could not extract _phase11_build_local_images"

MOCK_DOCKER="$TMP_DIR/docker"
CALL_LOG="$TMP_DIR/docker.calls"
COMPOSE_UP_MARKER="$TMP_DIR/compose-up.called"
cat > "$MOCK_DOCKER" <<'MOCK'
#!/usr/bin/env bash
set -u

printf '%s\n' "$*" >> "$MOCK_DOCKER_CALL_LOG"
args=" $* "
case "$args" in
    *" build --no-cache comfyui "*)
        exit 2
        ;;
    *" config --format json "*)
        cat <<'JSON'
{"name":"ods","services":{"comfyui":{"build":{"context":"extensions/services/comfyui"}}}}
JSON
        exit 0
        ;;
    *" image inspect ods-comfyui "*)
        # Simulate the stale image that masked the failed Spark build.
        exit 0
        ;;
    *" up -d "*)
        : > "$MOCK_COMPOSE_UP_MARKER"
        exit 0
        ;;
esac
exit 0
MOCK
chmod +x "$MOCK_DOCKER"

export MOCK_DOCKER_CALL_LOG="$CALL_LOG"
export MOCK_COMPOSE_UP_MARKER="$COMPOSE_UP_MARKER"
export DOCKER_CMD="$MOCK_DOCKER"
export DOCKER_COMPOSE_CMD="$MOCK_DOCKER compose"
COMPOSE_FLAGS_ARR=(
    -f docker-compose.base.yml
    -f extensions/services/comfyui/compose.yaml
    -f extensions/services/comfyui/compose.nvidia.yaml
)
export LOG_FILE="$TMP_DIR/install.log"
export AMB=""
export NC=""
export BGRN=""

ai_bad() { printf 'ERROR: %s\n' "$*" >> "$LOG_FILE"; }
ai() { printf '%s\n' "$*" >> "$LOG_FILE"; }
spin_task() {
    local pid="$1" rc=0
    wait "$pid" || rc=$?
    return "$rc"
}

set +e
if _phase11_build_local_images comfyui; then
    $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" \
        up -d --remove-orphans --no-build --pull never
    phase_rc=0
else
    phase_rc=$?
fi
set -e

[[ "$phase_rc" -ne 0 ]] \
    || fail "failed ComfyUI build returned success because a stale image existed"
[[ ! -e "$COMPOSE_UP_MARKER" ]] \
    || fail "compose up ran after a requested local image build failed"
grep -q 'build --no-cache comfyui' "$CALL_LOG" \
    || fail "mock did not observe the requested no-cache build"
grep -q 'image inspect ods-comfyui' "$CALL_LOG" \
    || fail "test did not exercise the stale-image branch"
grep -q 'Required local image build(s) failed: comfyui' "$LOG_FILE" \
    || fail "installer did not report the failed requested image"
pass "stale image cannot mask a failed requested local build"

guard_line="$(grep -nF '_phase11_build_local_images "${_build_services[@]}"' "$PHASE11" | head -1 | cut -d: -f1)"
compose_line="$(grep -n 'up -d --remove-orphans --no-build --pull never >>' "$PHASE11" | head -1 | cut -d: -f1)"
[[ -n "$guard_line" && -n "$compose_line" && "$guard_line" -lt "$compose_line" ]] \
    || fail "fatal local-build guard must run before compose up"
! grep -q '_retained_failed_build_services\|_excluded_build_services' "$PHASE11" \
    || fail "obsolete partial exclusion state can reintroduce failed services"
pass "phase 11 fails closed before compose launch"
