#!/usr/bin/env bash
set -euo pipefail

# Exercise the complete Docker phase, including its sudo fallback, without
# installing packages, changing groups or starting the developer's Docker.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/bin" "$TEST_ROOT/home"
export TEST_ROOT

cat > "$TEST_ROOT/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
    --version) echo 'Docker version 29.2.1' ;;
    'version --format '*) echo '29.2.1' ;;
    'compose version'|version) : ;;
    info)
        if [[ "${PRIVILEGED_DOCKER:-false}" == true ]]; then
            echo privileged-info >> "$TEST_ROOT/calls"
            exit 0
        fi
        if [[ "$USER" != pixel_owner ]]; then
            echo core-info-denied >> "$TEST_ROOT/calls"
            exit 13
        fi
        echo owner-info >> "$TEST_ROOT/calls"
        # The same environment as bootstrap, without printing it in reports.
        [[ "$HOME" == "$TEST_ROOT/home" && "$USER" == pixel_owner && "$LOGNAME" == pixel_owner ]]
        [[ "$DOCKER_HOST" == unix:///fixture/docker.sock && "$DOCKER_CONTEXT" == fixture-context ]]
        [[ "$DOCKER_CONFIG" == "$TEST_ROOT/config" ]]
        echo 'PRIVATE_DOCKER_INFO_MUST_NOT_BE_LOGGED'
        case "$PROBE_CASE" in
            success|different_owner) : ;;
            refreshed) [[ "${GROUP_REFRESHED:-false}" == true ]] ;;
            denied) echo 'permission denied while trying to connect to the Docker daemon socket' >&2; exit 13 ;;
            context) echo 'context fixture-context: context not found' >&2; exit 1 ;;
            daemon) echo 'Cannot connect to the Docker daemon at unix:///fixture/docker.sock' >&2; exit 1 ;;
            *) echo 'unexpected owner probe' >&2; exit 99 ;;
        esac
        ;;
    *) echo "unexpected Docker operation: $*" >&2; exit 99 ;;
esac
SH
cat > "$TEST_ROOT/bin/sudo" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == docker ]] || exit 99
shift
PRIVILEGED_DOCKER=true exec docker "$@"
SH
chmod +x "$TEST_ROOT/bin/"*

run_phase() (
    export PATH="$TEST_ROOT/bin:$PATH"
    export PROBE_CASE="$1"
    export DOCKER_HOST=unix:///fixture/docker.sock DOCKER_CONTEXT=fixture-context
    export DOCKER_CONFIG="$TEST_ROOT/config"
    SCRIPT_DIR="$ROOT" SKIP_DOCKER=true DRY_RUN=false INTERACTIVE=false
    GPU_COUNT=0 GPU_BACKEND=cpu PKG_MANAGER=apt LOG_FILE="$TEST_ROOT/install.log"
    # Reproduce the gap: the core Docker phase can pass as root while Pixel
    # needs the unprivileged owner's access, independently of DOCKER_CMD.
    DOCKER_CMD='' DOCKER_COMPOSE_CMD=''
    ENABLE_PIXEL_RUNTIME=true PIXEL_SERVICE_USER=pixel_owner
    [[ "$PROBE_CASE" != disabled ]] || ENABLE_PIXEL_RUNTIME=false
    [[ "$PROBE_CASE" != dry_run ]] || DRY_RUN=true
    [[ "$PROBE_CASE" != unset_flag ]] || unset ENABLE_PIXEL_RUNTIME

    ods_progress() { :; }
    show_phase() { :; }
    ai() { printf '%s\n' "$*"; }
    ai_ok() { printf '%s\n' "$*"; }
    ai_warn() { printf '%s\n' "$*"; }
    ai_bad() { printf '%s\n' "$*"; }
    log() { printf '%s\n' "$*"; }
    warn() { printf '%s\n' "$*"; }
    error() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
    ods_sudo_available() { return 0; }
    ods_sudo() {
        [[ "$PROBE_CASE" == different_owner && "$1" == -u && "$2" == pixel_owner && "$3" == -- ]] \
            || { echo 'UNEXPECTED host mutation' >&2; exit 99; }
        shift 3
        "$@"
    }
    # shellcheck source=installers/lib/pixel-host-install.sh
    source "$ROOT/installers/lib/pixel-host-install.sh"
    ods_pixel_owner_home() {
        [[ "$1" == pixel_owner ]]
        printf '%s\n' "$TEST_ROOT/home"
    }
    id() {
        case "$*" in
            -un)
                if [[ "$PROBE_CASE" == different_owner ]]; then echo installer_root; else echo pixel_owner; fi ;;
            -nG) echo users ;;
            '-nG pixel_owner')
                if [[ "$PROBE_CASE" == refreshed ]]; then echo 'users docker'; else echo users; fi ;;
            *) echo 'UNEXPECTED identity lookup' >&2; return 99 ;;
        esac
    }
    # Observe that the existing owner helper requests the new docker group;
    # argv transport itself is covered by test-pixel-host-install.sh.
    sg() {
        [[ "$1" == docker && "$2" == -c && -n "$ODS_PIXEL_OWNER_ARGV_JSON" ]]
        GROUP_REFRESHED=true HOME="$TEST_ROOT/home" USER=pixel_owner LOGNAME=pixel_owner docker info
    }
    # shellcheck source=installers/phases/05-docker.sh
    source "$ROOT/installers/phases/05-docker.sh"
    echo REACHED_NEXT_PHASE
)

for probe_case in denied success context daemon disabled dry_run unset_flag refreshed different_owner; do
    : > "$TEST_ROOT/calls"
    status=0
    # Run in a separate process to preserve production errexit semantics.
    export -f run_phase
    export ROOT
    bash -c 'set -euo pipefail; run_phase "$1"' bash "$probe_case" > "$TEST_ROOT/output" 2>&1 || status=$?
    case "$probe_case" in
        denied|context|daemon)
            [[ "$status" != 0 ]] || { cat "$TEST_ROOT/output"; echo "FAIL: $probe_case continued"; exit 1; }
            ! grep -q REACHED_NEXT_PHASE "$TEST_ROOT/output"
            grep -q privileged-info "$TEST_ROOT/calls"
            grep -q owner-info "$TEST_ROOT/calls"
            case "$probe_case" in
                denied) grep -q 'permission denied while trying to connect' "$TEST_ROOT/output" ;;
                context) grep -q 'context fixture-context: context not found' "$TEST_ROOT/output" ;;
                daemon) grep -q 'Cannot connect to the Docker daemon' "$TEST_ROOT/output" ;;
            esac
            grep -q 'pixel_owner' "$TEST_ROOT/output"
            grep -q 'docker info >/dev/null' "$TEST_ROOT/output"
            grep -q 'TROUBLESHOOTING.md#pixel-cannot-access-docker' "$TEST_ROOT/output"
            ;;
        *)
            [[ "$status" == 0 ]] || { cat "$TEST_ROOT/output"; echo "FAIL: $probe_case stopped ($status)"; exit 1; }
            grep -q REACHED_NEXT_PHASE "$TEST_ROOT/output"
            case "$probe_case" in
                disabled|dry_run|unset_flag) ! grep -q owner-info "$TEST_ROOT/calls" ;;
                *) grep -q owner-info "$TEST_ROOT/calls" ;;
            esac
            ;;
    esac
    ! grep -qE 'PRIVATE_DOCKER_INFO_MUST_NOT_BE_LOGGED|UNEXPECTED' "$TEST_ROOT/output"
    echo "PASS: Pixel owner Docker preflight: $probe_case"
done
