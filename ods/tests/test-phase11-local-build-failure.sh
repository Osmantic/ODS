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
    *" build comfyui "*)
        printf '%s\n' "$*" >> "$MOCK_COMFYUI_BUILD_LOG"
        count=0
        if [[ -f "$MOCK_COMFYUI_BUILD_COUNT" ]]; then
            count=$(cat "$MOCK_COMFYUI_BUILD_COUNT")
        fi
        count=$((count + 1))
        printf '%s\n' "$count" > "$MOCK_COMFYUI_BUILD_COUNT"
        printf '%s\n' "${MOCK_BUILD_OUTPUT:-}"
        if [[ "${MOCK_COMFYUI_ALWAYS_FAIL:-false}" == "true" ]]; then
            exit 2
        fi
        fail_before="${MOCK_COMFYUI_FAIL_BEFORE_SUCCESS:-0}"
        if (( count <= fail_before )); then
            exit 2
        fi
        exit 0
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
    *" info "*)
        [[ "${MOCK_DOCKER_DOWN:-false}" != "true" ]] || exit 1
        exit 0
        ;;
esac
exit 0
MOCK
chmod +x "$MOCK_DOCKER"

export MOCK_DOCKER_CALL_LOG="$CALL_LOG"
export MOCK_COMPOSE_UP_MARKER="$COMPOSE_UP_MARKER"
export MOCK_COMFYUI_BUILD_COUNT="$TMP_DIR/comfyui-build.count"
export MOCK_COMFYUI_BUILD_LOG="$TMP_DIR/comfyui-build.log"
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
export ODS_DOCKER_BUILD_RETRY_DELAY_SECONDS=0

ai_bad() { printf 'ERROR: %s\n' "$*" >> "$LOG_FILE"; }
ai() { printf '%s\n' "$*" >> "$LOG_FILE"; }
ai_warn() { printf 'WARN: %s\n' "$*" >> "$LOG_FILE"; }
ui_status_line() { printf '[%s] %s\n' "$1" "$2"; }
spin_task() {
    local pid="$1" rc=0
    wait "$pid" || rc=$?
    return "$rc"
}

export MOCK_COMFYUI_ALWAYS_FAIL=true
export ODS_DOCKER_BUILD_MAX_ATTEMPTS=1
set +e
if _phase11_build_local_images comfyui; then
    $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" \
        up -d --remove-orphans --no-build --pull never
    phase_rc=0
else
    phase_rc=$?
fi
set -e
unset MOCK_COMFYUI_ALWAYS_FAIL

[[ "$phase_rc" -ne 0 ]] \
    || fail "failed ComfyUI build returned success because a stale image existed"
[[ ! -e "$COMPOSE_UP_MARKER" ]] \
    || fail "compose up ran after a requested local image build failed"
grep -q 'build comfyui' "$CALL_LOG" \
    || fail "mock did not observe the requested local build"
! grep -q 'build --no-cache' "$CALL_LOG" \
    || fail "local build discarded reusable Docker layers"
! grep -q 'image inspect ods-comfyui' "$CALL_LOG" \
    || fail "failed build should not consult a stale image"
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

: > "$CALL_LOG"
: > "$LOG_FILE"
rm -f "$MOCK_COMFYUI_BUILD_COUNT" "$COMPOSE_UP_MARKER"
export MOCK_COMFYUI_FAIL_BEFORE_SUCCESS=1
export ODS_DOCKER_BUILD_MAX_ATTEMPTS=2

set +e
_phase11_build_local_images comfyui
phase_rc=$?
set -e

[[ "$phase_rc" -eq 0 ]] \
    || fail "transient local image build failure was not retried to success"
[[ "$(cat "$MOCK_COMFYUI_BUILD_COUNT")" -eq 2 ]] \
    || fail "expected two build attempts for a one-time transient failure"
grep -q 'WARN: comfyui build failed; retrying' "$LOG_FILE" \
    || fail "retry warning was not recorded"
pass "phase 11 retries transient local image build failures"

: > "$CALL_LOG"
: > "$LOG_FILE"
rm -f "$MOCK_COMFYUI_BUILD_COUNT"
unset MOCK_COMFYUI_FAIL_BEFORE_SUCCESS
export MOCK_COMFYUI_ALWAYS_FAIL=true
export MOCK_DOCKER_DOWN=true
export ODS_DOCKER_BUILD_MAX_ATTEMPTS=3

set +e
_phase11_build_local_images comfyui
phase_rc=$?
set -e
unset MOCK_COMFYUI_ALWAYS_FAIL MOCK_DOCKER_DOWN

[[ "$phase_rc" -ne 0 ]] \
    || fail "a build with Docker stopped returned success"
[[ "$(cat "$MOCK_COMFYUI_BUILD_COUNT")" -eq 1 ]] \
    || fail "the build was retried while Docker was stopped"
grep -q 'ERROR: Docker stopped responding while comfyui was building.' "$LOG_FILE" \
    || fail "the stopped Docker was not named as the cause"
grep -q 'sudo systemctl start docker' "$LOG_FILE" \
    || fail "no way to start Docker again was given"
! grep -q 'Required local image build(s) failed' "$LOG_FILE" \
    || fail "a stopped Docker was reported as a failed build"
pass "phase 11 names a stopped Docker instead of a failed build"

# A container-only DNS failure passes the host curl preflight. Feed actual
# package-manager error shapes through the build path without a Docker daemon.
for diagnostic in \
    'npm error code EAI_AGAIN' \
    "Temporary failure resolving 'deb.debian.org'" \
    'pip: Temporary failure in name resolution' \
    'curl: (6) Could not resolve host: registry.npmjs.org'; do
    : > "$LOG_FILE"
    : > "$CALL_LOG"
    rm -f "$MOCK_COMFYUI_BUILD_COUNT"
    export MOCK_COMFYUI_ALWAYS_FAIL=true MOCK_BUILD_OUTPUT="$diagnostic"
    export ODS_DOCKER_BUILD_MAX_ATTEMPTS=2
    if _phase11_build_local_images comfyui; then
        fail "DNS build failure was reported as success"
    fi
    [[ "$(cat "$MOCK_COMFYUI_BUILD_COUNT")" == 2 ]] || fail "DNS hint changed retries"
    grep -q 'Build log contains a name-resolution error' "$LOG_FILE" || fail "DNS diagnostic missing: $diagnostic"
    grep -q '127.0.0.53' "$LOG_FILE" || fail "stub diagnostic missing"
    grep -q 'INSTALL-TROUBLESHOOTING.md#container-build-dns' "$LOG_FILE" || fail "DNS recovery guide missing"
    ! grep -Eq ' run |--dns|--network|systemctl|daemon.json' "$CALL_LOG" || fail "diagnostic mutated runtime or ran a probe"
done
pass "DNS build errors get guidance without changing retries or runtime"

for diagnostic in 'npm error Exit handler never called!' 'npm error code ERESOLVE' 'getaddrinfo example in source code'; do
    : > "$LOG_FILE"
    export MOCK_BUILD_OUTPUT="$diagnostic"
    if _phase11_build_local_images comfyui; then fail "unrelated build failure passed"; fi
    ! grep -q 'Build log contains a name-resolution error' "$LOG_FILE" || fail "unrelated error called DNS: $diagnostic"
done
pass "generic npm and unrelated errors are not classified as DNS"

: > "$LOG_FILE"
unset MOCK_COMFYUI_ALWAYS_FAIL
export MOCK_BUILD_OUTPUT='npm error code EAI_AGAIN'
if ! _phase11_build_local_images comfyui; then fail "successful build failed"; fi
! grep -q 'Build log contains a name-resolution error' "$LOG_FILE" || fail "successful build got failure guidance"
pass "successful builds are unaffected by diagnostic-looking output"
