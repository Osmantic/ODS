#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE08="$ROOT_DIR/installers/phases/08-images.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

helper="$(sed -n '/^_phase08_check_docker_data_root_space() {/,/^}/p' "$PHASE08")"
[[ -n "$helper" ]] || fail "Docker data-root space check is missing"
eval "$helper"

DOCKER_CMD=mock_docker
LOG_FILE=/dev/null
MOCK_DOCKER_ROOT_DIR="$TMP_DIR/docker-root"
MOCK_PODMAN_ROOT_DIR="$TMP_DIR/podman-root"
MOCK_EXPECTED_ROOT="$MOCK_DOCKER_ROOT_DIR"
MOCK_DOCKER_ROOT_KB=14680064
mkdir -p "$MOCK_DOCKER_ROOT_DIR" "$MOCK_PODMAN_ROOT_DIR"
mock_docker() {
    [[ "$1" == info && "$2" == --format ]] || fail "unexpected Docker invocation: $*"
    case "$3" in
        '{{.DockerRootDir}}') printf '%s\n' "$MOCK_DOCKER_ROOT_DIR" ;;
        '{{.Store.GraphRoot}}') printf '%s\n' "$MOCK_PODMAN_ROOT_DIR" ;;
        *) fail "unexpected storage format: $3" ;;
    esac
}
uname() { printf 'Linux\n'; }
df() {
    [[ "$2" == "$MOCK_EXPECTED_ROOT" ]] || fail "space check used the wrong filesystem path: $2"
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    printf '/dev/mock 20000000 1 %s 1%% %s\n' "$MOCK_DOCKER_ROOT_KB" "$2"
}
ai_bad() { printf 'ERROR: %s\n' "$*"; }
ai() { printf 'INFO: %s\n' "$*"; }

if _phase08_check_docker_data_root_space >/dev/null; then
    fail "low Docker data-root space should block image downloads"
fi
pass "low Docker data-root capacity blocks before image pulls"

MOCK_DOCKER_ROOT_KB=16777216
_phase08_check_docker_data_root_space >/dev/null \
    || fail "sufficient Docker data-root space was rejected"
pass "sufficient Docker data-root capacity permits image downloads"

MOCK_DOCKER_ROOT_DIR=""
MOCK_EXPECTED_ROOT="$MOCK_PODMAN_ROOT_DIR"
_phase08_check_docker_data_root_space >/dev/null \
    || fail "Podman-compatible Docker CLI storage root was not recognized"
pass "Podman GraphRoot is recognized by the Docker-compatible CLI"

MOCK_PODMAN_ROOT_DIR=""
MOCK_DOCKER_ROOT_DIR=""
MOCK_EXPECTED_ROOT=""
if _phase08_check_docker_data_root_space >/dev/null; then
    fail "unknown Docker data-root should fail closed"
fi
pass "unknown Docker data-root fails closed before image pulls"

check_line="$(grep -n 'if ! _phase08_check_docker_data_root_space' "$PHASE08" | cut -d: -f1)"
pull_line="$(grep -n 'if ! pull_with_progress "\$img"' "$PHASE08" | cut -d: -f1)"
[[ "$check_line" =~ ^[0-9]+$ && "$pull_line" =~ ^[0-9]+$ && "$check_line" -lt "$pull_line" ]] \
    || fail "Docker data-root check does not run before pull_with_progress"
pass "production image-download flow checks Docker storage before pulling"
