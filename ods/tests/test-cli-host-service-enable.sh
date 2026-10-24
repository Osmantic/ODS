#!/usr/bin/env bash
# Regression (#7626): `ods enable opencode` failed with "No Compose selection
# file for opencode". OpenCode is a host service (type: host-systemd) with no
# Compose fragment, so enable/disable must explain where it is managed instead
# of handing it to the Compose selection helper.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

make_install() {
    local install_dir="$1"
    mkdir -p \
        "$install_dir/lib" \
        "$install_dir/scripts" \
        "$install_dir/data" \
        "$install_dir/extensions/services/opencode"

    cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
    cp "$ROOT_DIR/lib/service-registry.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/python-cmd.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/scripts/extension-selection.py" "$install_dir/scripts/"
    cp "$ROOT_DIR/extensions/services/opencode/manifest.yaml" \
        "$install_dir/extensions/services/opencode/"
    printf 'services: {}\n' > "$install_dir/docker-compose.base.yml"
    printf '%s\n' 'GPU_BACKEND=cpu' 'GPU_COUNT=1' 'ODS_MODE=local' 'TIER=1' \
        > "$install_dir/.env"
}

install_dir="$TMP_DIR/install"
make_install "$install_dir"

for action in enable disable; do
    for name in opencode opencode-web; do
        status=0
        output="$(ODS_HOME="$install_dir" bash "$install_dir/ods-cli" "$action" "$name" 2>&1)" \
            || status=$?
        [[ "$status" -eq 1 ]] \
            || fail "'ods $action $name' exited $status instead of 1: $output"
        [[ "$output" != *"No Compose selection file"* ]] \
            || fail "'ods $action $name' still reports the Compose selection error: $output"
        [[ "$output" == *"opencode runs on the host, not as a Compose extension; 'ods $action' does not apply"* ]] \
            || fail "'ods $action $name' did not explain that OpenCode is a host service: $output"
        [[ "$output" == *"/apps/opencode"* && "$output" == *"--opencode"* ]] \
            || fail "'ods $action $name' did not say where OpenCode is managed: $output"
    done
done
[[ ! -e "$install_dir/extensions/services/opencode/compose.yaml" \
    && ! -e "$install_dir/extensions/services/opencode/compose.yaml.disabled" ]] \
    || fail "host service guidance created a Compose selection file"

pass "enable/disable explain that OpenCode is managed outside Compose"
