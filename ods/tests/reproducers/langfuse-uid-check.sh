#!/usr/bin/env bash
# Reproducer for langfuse postgres uid mismatch on Linux native Docker.
#
# Requires: docker, sudo.
#
# Purpose: demonstrate that postgres:17.9-alpine fails to initdb when its
# data bind-mount is owned by the host install user (uid != 70), and that
# chowning the dir to 70:70 (as the langfuse post_install hook does on
# Linux) fixes it. macOS Docker Desktop masks the uid difference via
# osxfs/virtiofs so the "bug" branch passes trivially there — this
# reproducer is meaningful only on Linux.
#
# Usage:
#   bash ods/tests/reproducers/langfuse-uid-check.sh
#
# Exit codes:
#   0 — observed result matched platform expectation
#   1 — observed result did NOT match platform expectation
#   2 — prerequisite missing (docker/sudo)

set -euo pipefail

PLATFORM="$(uname -s)"
TMPDIR_REPRO="/tmp/ods-3E-repro-$$"
POSTGRES_IMAGE="postgres:17.9-alpine"

log() {
    echo "[repro] $*" >&2
}

# shellcheck disable=SC2329  # invoked via `trap cleanup EXIT` below
cleanup() {
    local rc=$?
    if [[ -d "$TMPDIR_REPRO" ]]; then
        # Dir may now be owned by uid 70 after chown, so use sudo if available.
        if command -v sudo >/dev/null 2>&1; then
            sudo rm -rf "$TMPDIR_REPRO" 2>/dev/null || rm -rf "$TMPDIR_REPRO" 2>/dev/null || true
        else
            rm -rf "$TMPDIR_REPRO" 2>/dev/null || true
        fi
    fi
    exit "$rc"
}
trap cleanup EXIT

# --- Prerequisites ---------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    log "ERROR: docker not found; this reproducer requires Docker"
    exit 2
fi
if ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: sudo not found; this reproducer requires sudo to chown to uid 70"
    exit 2
fi

log "platform: $PLATFORM"
log "postgres image: $POSTGRES_IMAGE"

# Seed temp dir owned by current user (install-user simulation).
mkdir -p "$TMPDIR_REPRO"
log "created $TMPDIR_REPRO owned by $(id -un):$(id -gn) (uid $(id -u))"

# Pull image up front so later timeouts aren't image-pull delays.
log "pulling $POSTGRES_IMAGE (if not cached)"
docker pull "$POSTGRES_IMAGE" >/dev/null

# --- Phase 1: bug branch — directory owned by install user -----------------

log "phase 1: starting postgres with install-user-owned bind mount"
set +e
docker run --rm \
    -e POSTGRES_PASSWORD=reproducer \
    -v "$TMPDIR_REPRO":/var/lib/postgresql/data \
    --name "ods-3E-repro-phase1-$$" \
    "$POSTGRES_IMAGE" \
    postgres -c 'shared_buffers=16MB' >/tmp/ods-3E-repro-phase1.log 2>&1 &
P1_PID=$!
# Give the container ~10s to fail or succeed initdb; we only need the
# initial verdict, not a long-running DB.
sleep 10
if kill -0 "$P1_PID" 2>/dev/null; then
    # Still running — on macOS / WSL2 Docker Desktop this is the happy path.
    log "phase 1: container still running after 10s"
    docker stop "ods-3E-repro-phase1-$$" >/dev/null 2>&1 || true
    wait "$P1_PID" 2>/dev/null || true
    PHASE1_RESULT="running"
else
    wait "$P1_PID" 2>/dev/null
    PHASE1_RC=$?
    log "phase 1: container exited rc=$PHASE1_RC"
    if grep -qi "permission denied\|could not\|cannot create" /tmp/ods-3E-repro-phase1.log; then
        PHASE1_RESULT="permission_denied"
    else
        PHASE1_RESULT="exited_other"
    fi
fi
set -e
log "phase 1 result: $PHASE1_RESULT"

# --- Phase 2: fix branch — chown to 70:70 then retry -----------------------

log "phase 2: chown -R 70:70 $TMPDIR_REPRO (simulating post_install hook)"
sudo chown -R 70:70 "$TMPDIR_REPRO"

# Wipe any partial state from phase 1 so initdb runs cleanly.
sudo rm -rf "$TMPDIR_REPRO"
mkdir -p "$TMPDIR_REPRO"
sudo chown -R 70:70 "$TMPDIR_REPRO"

log "phase 2: starting postgres with chowned bind mount"
set +e
docker run --rm \
    -e POSTGRES_PASSWORD=reproducer \
    -v "$TMPDIR_REPRO":/var/lib/postgresql/data \
    --name "ods-3E-repro-phase2-$$" \
    "$POSTGRES_IMAGE" \
    postgres -c 'shared_buffers=16MB' >/tmp/ods-3E-repro-phase2.log 2>&1 &
P2_PID=$!
sleep 10
if kill -0 "$P2_PID" 2>/dev/null; then
    log "phase 2: container still running after 10s (initdb succeeded)"
    docker stop "ods-3E-repro-phase2-$$" >/dev/null 2>&1 || true
    wait "$P2_PID" 2>/dev/null || true
    PHASE2_RESULT="running"
else
    wait "$P2_PID" 2>/dev/null
    PHASE2_RC=$?
    log "phase 2: container exited rc=$PHASE2_RC"
    PHASE2_RESULT="exited"
fi
set -e
log "phase 2 result: $PHASE2_RESULT"

# --- Verdict ---------------------------------------------------------------

echo
echo "============================================================"
echo "  Reproducer verdict (platform: $PLATFORM)"
echo "============================================================"
echo "  Phase 1 (install-user-owned): $PHASE1_RESULT"
echo "  Phase 2 (chowned to 70:70):   $PHASE2_RESULT"
echo "============================================================"

case "$PLATFORM" in
    Linux)
        if [[ "$PHASE1_RESULT" == "permission_denied" && "$PHASE2_RESULT" == "running" ]]; then
            echo "PASS — Linux bug reproduced and hook-equivalent chown fixes it."
            exit 0
        fi
        echo "FAIL — expected phase1=permission_denied, phase2=running on Linux."
        echo "  See /tmp/ods-3E-repro-phase1.log and /tmp/ods-3E-repro-phase2.log"
        exit 1
        ;;
    Darwin)
        if [[ "$PHASE1_RESULT" == "running" && "$PHASE2_RESULT" == "running" ]]; then
            echo "PASS — macOS Docker Desktop masks uid as expected; both phases ran."
            echo "NOTE: this platform does not exercise the Linux bug path."
            exit 0
        fi
        echo "FAIL — expected both phases to run on macOS (uid translation)."
        exit 1
        ;;
    *)
        echo "UNKNOWN platform ($PLATFORM) — report results to maintainer."
        exit 1
        ;;
esac
