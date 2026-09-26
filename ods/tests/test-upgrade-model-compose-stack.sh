#!/usr/bin/env bash
# upgrade-model.sh must restart llama-server through this host's real Compose
# stack. Every GPU overlay file ships on every install, so choosing the first
# overlay that exists sent NVIDIA and CPU hosts through docker-compose.amd.yml.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE="$ROOT_DIR/scripts/upgrade-model.sh"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

function_block() {
    awk -v signature="^${2}[(][)]" '
        $0 ~ signature { in_block=1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$1"
}

detect_block="$(function_block "$UPGRADE" detect_compose_file)"
[[ -n "$detect_block" ]] || fail "detect_compose_file not found in $UPGRADE"
eval "$detect_block"

# A fresh install tree: base plus every shipped GPU overlay.
make_install() {
    local dir="$WORK_DIR/$1"
    mkdir -p "$dir"
    local overlay
    for overlay in base amd nvidia cpu apple intel; do
        : > "$dir/docker-compose.$overlay.yml"
    done
    printf '%s\n' "$dir"
}

assert_args() {
    local label="$1"
    shift
    local expected actual
    expected="$(printf '%s\n' "$@")"
    actual="$(printf '%s\n' "${COMPOSE_FILE_ARGS[@]}")"
    [[ "$actual" == "$expected" ]] || fail "$label: got [${COMPOSE_FILE_ARGS[*]}], want [$*]"
    echo "[PASS] $label"
}

# 1. The installer-recorded stack wins, including extension compose files.
ODS_DIR="$(make_install recorded)"
printf 'GPU_BACKEND=amd\n' > "$ODS_DIR/.env"
printf '%s\n' "-f docker-compose.base.yml -f docker-compose.nvidia.yml -f extensions/services/n8n/compose.yaml" \
    > "$ODS_DIR/.compose-flags"
detect_compose_file
assert_args "recorded .compose-flags stack is used verbatim" \
    -f "$ODS_DIR/docker-compose.base.yml" \
    -f "$ODS_DIR/docker-compose.nvidia.yml" \
    -f "$ODS_DIR/extensions/services/n8n/compose.yaml"

# 2-4. Without a recorded stack, the overlay follows GPU_BACKEND, not the
# first overlay file that happens to exist.
for backend in nvidia cpu amd; do
    ODS_DIR="$(make_install "backend-$backend")"
    printf 'GPU_BACKEND="%s"\n' "$backend" > "$ODS_DIR/.env"
    detect_compose_file
    assert_args "GPU_BACKEND=$backend selects its own overlay" \
        -f "$ODS_DIR/docker-compose.base.yml" \
        -f "$ODS_DIR/docker-compose.$backend.yml"
done

# 5. Pre-split installs with a single docker-compose.yml keep working.
ODS_DIR="$WORK_DIR/legacy"
mkdir -p "$ODS_DIR"
: > "$ODS_DIR/docker-compose.yml"
detect_compose_file
assert_args "legacy single-file install" -f "$ODS_DIR/docker-compose.yml"

echo "[PASS] upgrade-model.sh restarts through the host's Compose stack"
