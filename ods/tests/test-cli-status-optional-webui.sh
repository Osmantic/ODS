#!/usr/bin/env bash
# Contract: lean and gateway-only installs leave Open WebUI out of the stack
# (ENABLE_OPEN_WEBUI=false). `ods status` and `ods status --json` must not
# report it as a core service that is "not responding"; once the Library
# add-back sets ENABLE_OPEN_WEBUI=true it is checked again.
#
# Run from repo root:  bash ods/tests/test-cli-status-optional-webui.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

helper_src="$(sed -n '/^_ods_cli_service_not_installed()/,/^}/p' "$CLI")"
[[ -n "$helper_src" ]] || fail "could not extract _ods_cli_service_not_installed from ods-cli"
eval "$helper_src"

INSTALL_DIR="$(mktemp -d)"
trap 'rm -rf "$INSTALL_DIR"' EXIT
mkdir -p "$INSTALL_DIR/extensions/services/dashboard" "$INSTALL_DIR/extensions/services/open-webui"

export ENABLE_OPEN_WEBUI=false
_ods_cli_service_not_installed open-webui || fail "disabled Open WebUI is still treated as installed"
_ods_cli_service_not_installed dashboard && fail "a disabled Open WebUI flag hid another core service"
pass "ENABLE_OPEN_WEBUI=false leaves Open WebUI out of status"

export ENABLE_OPEN_WEBUI=true
_ods_cli_service_not_installed open-webui && fail "enabled Open WebUI was skipped"
unset ENABLE_OPEN_WEBUI
_ods_cli_service_not_installed open-webui && fail "an install without the flag must keep checking Open WebUI"
pass "enabled or unset Open WebUI is still checked"

relay="$INSTALL_DIR/extensions/services/pixel-model-relay"
mkdir -p "$relay"
: > "$relay/compose.yaml.disabled"
_ods_cli_service_not_installed pixel-model-relay || fail "a core service with a disabled compose file is still treated as installed"
_ods_cli_service_not_installed dashboard && fail "a core service without a compose file was skipped"
# Native macOS Pixel deliberately selects this disabled-named fragment. Its
# filename is not evidence of absence when the actual stack includes it.
_ods_cli_service_not_installed pixel-model-relay -f docker-compose.base.yml \
    -f extensions/services/pixel-model-relay/compose.yaml.disabled \
    && fail "native Pixel's selected disabled-named relay was skipped"
_ods_cli_service_not_installed pixel-model-relay -f "$relay/compose.yaml.disabled" \
    && fail "an absolute selected relay fragment was skipped"
_ods_cli_service_not_installed pixel-model-relay -f ./extensions/services/pixel-model-relay/compose.yaml.disabled \
    && fail "a dot-relative selected relay fragment was skipped"
_ods_cli_service_not_installed pixel-model-relay -f extensions/services/another/compose.yaml.disabled \
    || fail "an unrelated fragment incorrectly enabled the relay health check"
pass "selected disabled-named native Pixel fragments remain visible in status"
: > "$relay/compose.yaml"
_ods_cli_service_not_installed pixel-model-relay && fail "an enabled core extension was skipped"
pass "core extensions the installer left disabled are left out of status"

for fn in cmd_status cmd_status_json; do
    body="$(sed -n "/^${fn}()/,/^}/p" "$CLI")"
    [[ -n "$body" ]] || fail "could not extract $fn from ods-cli"
    grep -qF '_ods_cli_service_not_installed "$sid" "${flags[@]}" && continue' <<<"$body" \
        || fail "$fn does not skip services the installation left out"
done
pass "ods status and ods status --json both skip services the installation left out"

echo "All ods status optional Open WebUI checks passed."
