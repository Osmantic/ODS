#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_root="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
scratch="$(mktemp -d "$tmp_root/ods-remote-provider-selection.XXXXXX")"
cleanup() {
    # Limit recursive cleanup to the directory this test just created.
    [[ "$scratch" == "$tmp_root"/ods-remote-provider-selection.* ]] && rm -rf -- "$scratch"
}
trap cleanup EXIT

INSTALL_DIR="$scratch/install"
external_data="$scratch/External ODS Data/data"
mkdir -p "$INSTALL_DIR" "$external_data/remote-provider"
printf 'ODS_DATA_DIR="%s"\n' "$external_data" > "$INSTALL_DIR/.env"
cat > "$external_data/remote-provider/routing-state.json" <<'JSON'
{"schema":"ods.remote-routing-state.v1","enabled":true,"provider":{"transport":"ssh"}}
JSON

source "$root/lib/safe-env.sh"
source "$root/installers/macos/lib/env-generator.sh"
installer="$root/installers/macos/install-macos.sh"
eval "$(sed -n '/^_macos_remote_provider_retained_data_dir() {/,/^}/p' "$installer")"

retained_data="$(_macos_remote_provider_retained_data_dir)"
[[ "$retained_data" == "$external_data" ]] || {
    echo '[FAIL] quoted external ODS_DATA_DIR was not decoded' >&2
    exit 1
}
selection="$(python3 "$root/scripts/remote-provider-compose-selection.py" inspect \
    "$INSTALL_DIR" --data-dir "$retained_data")"
[[ "$selection" == *'"remote-provider-egress":"enabled"'* \
    && "$selection" == *'"remote-provider-ssh-tunnel":"enabled"'* ]] || {
    echo '[FAIL] retained SSH route in quoted external data path was not selected' >&2
    exit 1
}

printf 'ODS_DATA_DIR=%s\n' "$external_data" > "$INSTALL_DIR/.env"
[[ "$(_macos_remote_provider_retained_data_dir)" == "$external_data" ]] || {
    echo '[FAIL] unquoted external ODS_DATA_DIR was not retained' >&2
    exit 1
}
printf 'ODS_DATA_DIR=""\n' > "$INSTALL_DIR/.env"
[[ "$(_macos_remote_provider_retained_data_dir)" == "$INSTALL_DIR/data" ]] || {
    echo '[FAIL] quoted empty ODS_DATA_DIR did not select the default' >&2
    exit 1
}
rm "$INSTALL_DIR/.env"
[[ "$(_macos_remote_provider_retained_data_dir)" == "$INSTALL_DIR/data" ]] || {
    echo '[FAIL] fresh install data-root default changed' >&2
    exit 1
}
echo '[PASS] macOS remote-provider selection decodes retained external data roots'
