#!/usr/bin/env bash
# Exercise the installer's service-permission boundary and Docker fallback.
# The sourced phase step invokes the fixture functions below.
# shellcheck disable=SC2329
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
awk '/_phase06_step "prepare-service-permissions"/ {copy=1}
     /# .*\.env merge logic/ {copy=0}
     copy' "$ROOT/installers/phases/06-directories.sh" > "$work/permissions.sh"
[[ -s "$work/permissions.sh" ]]

run_case() (
    set -euo pipefail
    local scenario=$1
    INSTALL_DIR="$work/$scenario"
    mkdir -p "$INSTALL_DIR/data/token-spy" "$INSTALL_DIR/extensions/services/token-spy"
    touch "$INSTALL_DIR/extensions/services/token-spy/compose.yaml"
    _phase06_rootless=false
    [[ "$scenario" != rootless ]] || _phase06_rootless=true
    [[ "$scenario" != disabled ]] || rm "$INSTALL_DIR/extensions/services/token-spy/compose.yaml"
    _phase06_step() { :; }
    warn() { printf '%s\n' "$*" >&2; }
    error() { printf '%s\n' "$*" >&2; return 1; }
    ods_sudo_available() { [[ "$scenario" != no-sudo* && "$scenario" != already-owner ]]; }
    ods_sudo() { elevated=true "$@"; }
    id() {
        if [[ "${1:-}" == -u ]]; then
            [[ "$scenario" != already-owner ]] || { printf '1000\n'; return; }
            printf '1001\n'
        else
            command id "$@"
        fi
    }
    ODS_ROOTLESS_HELPER_IMAGE=busybox:fixture
    _ods_rootless_ensure_helper_image() { [[ "$scenario" != no-sudo-image-failure ]]; }
    docker_run() {
        printf 'docker:%s\n' "$*" >> "$INSTALL_DIR/calls"
        [[ "$scenario" != no-sudo-docker-failure ]] || return 1
        [[ "$*" == *'--network none --user 0:0'* ]]
        [[ "$*" == *"src=$INSTALL_DIR/data/token-spy,dst=/data"* ]]
        [[ "$*" == *'busybox:fixture chown -h -R 1000:1000 /data'* ]]
        [[ "$*" != *'--privileged'* ]]
        printf 'writable\n' > "$INSTALL_DIR/ownership-result"
    }
    chown() {
        printf '%s\n' "${elevated:-false}:$*" >> "$INSTALL_DIR/calls"
        [[ "$scenario" != sudo-failure ]] || return 1
        # A non-1000 install owner cannot change ownership without privilege.
        [[ "${elevated:-false}" == true || "$scenario" == already-owner ]] || return 1
        printf 'writable\n' > "$INSTALL_DIR/ownership-result"
    }
    # shellcheck disable=SC1091
    source "$work/permissions.sh"
    printf 'continued\n' > "$INSTALL_DIR/continued"
)

run_case fresh
test -f "$work/fresh/ownership-result"
echo 'PASS: non-1000 install owner gets privileged ownership repair'

for scenario in sudo-failure no-sudo-docker-failure no-sudo-image-failure; do
    status=0
    # Run in a separate shell context so checking status does not disable -e.
    ( run_case "$scenario" ) > "$work/$scenario.log" 2>&1 &
    pid=$!
    wait "$pid" || status=$?
    [[ "$status" != 0 && ! -e "$work/$scenario/continued" ]]
    echo "PASS: $scenario stops installation before configuration generation"
done

run_case no-sudo
test -f "$work/no-sudo/ownership-result"
grep -Fq 'docker:run --rm --network none --user 0:0' "$work/no-sudo/calls"
echo 'PASS: no-sudo Docker owner repair is scoped to token-spy data'

run_case already-owner
test -f "$work/already-owner/ownership-result"
echo 'PASS: matching owner can install without sudo'

for scenario in rootless disabled; do
    run_case "$scenario"
    test ! -e "$work/$scenario/calls"
    echo "PASS: $scenario leaves ownership to its existing lifecycle"
done
