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

export ENABLE_OPEN_WEBUI=false
_ods_cli_service_not_installed open-webui || fail "disabled Open WebUI is still treated as installed"
_ods_cli_service_not_installed dashboard && fail "a disabled Open WebUI flag hid another core service"
pass "ENABLE_OPEN_WEBUI=false leaves Open WebUI out of status"

export ENABLE_OPEN_WEBUI=true
_ods_cli_service_not_installed open-webui && fail "enabled Open WebUI was skipped"
unset ENABLE_OPEN_WEBUI
_ods_cli_service_not_installed open-webui && fail "an install without the flag must keep checking Open WebUI"
pass "enabled or unset Open WebUI is still checked"

for fn in cmd_status cmd_status_json; do
    body="$(sed -n "/^${fn}()/,/^}/p" "$CLI")"
    [[ -n "$body" ]] || fail "could not extract $fn from ods-cli"
    grep -q '_ods_cli_service_not_installed "\$sid" && continue' <<<"$body" \
        || fail "$fn does not skip services the installation left out"
done
pass "ods status and ods status --json both skip services the installation left out"

echo "All ods status optional Open WebUI checks passed."
