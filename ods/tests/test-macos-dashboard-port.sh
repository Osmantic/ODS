#!/usr/bin/env bash
# Contract: the macOS installer's readiness summary, its success card and
# `ods-macos.sh status` check the Dashboard on DASHBOARD_PORT from .env, the
# port Compose publishes it on. A fixed 3001 failed the install's required
# Dashboard check and reported the Dashboard as down when the owner moved it.
#
# The stubs below set globals that the eval'd installer and CLI code reads.
# shellcheck disable=SC2034
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }

eval "$(sed -n '/^read_env_value()/,/^}/p' "$CLI")"
readiness_src="$(awk '
    /^# Compose publishes the Dashboard on DASHBOARD_PORT/ { emit = 1 }
    emit { print }
    emit && /^} \| ods_readiness_summary / { exit }
' "$INSTALLER")"
[[ -n "$readiness_src" ]] || fail "could not extract the installer readiness summary"
# shellcheck disable=SC2016
grep -qx 'show_success_card 3000 "$_dashboard_port"' "$INSTALLER" \
    || fail "the installer success card does not link the configured Dashboard port"
status_src="$(sed -n '/^cmd_status()/,/^}/p' "$CLI")"
[[ -n "$status_src" ]] || fail "could not extract cmd_status from ods-macos.sh"
eval "$status_src"

INSTALL_DIR="$TMP_DIR/install"
mkdir -p "$INSTALL_DIR"
ODS_LOG_FILE="$TMP_DIR/install.log"
OPENCODE_PLIST="$TMP_DIR/missing.plist"
ENABLE_OPEN_WEBUI=false CLOUD_MODE=false ENABLE_PERPLEXICA=false ENABLE_VOICE=false
ENABLE_WORKFLOWS=false ENABLE_OPENCODE=false
_health_llama_host=127.0.0.1 _health_llama_port=8080
GRN="" DGRN="" WHT="" NC=""

ods_readiness_summary() {
    cat > "$TMP_DIR/rows"
    printf '%s\n' "$3" > "$TMP_DIR/summary-url"
}
run_readiness() { eval "$readiness_src"; }

# cmd_status collaborators.
test_install() { :; }
resolve_cli_llm_route() { CLI_LLM_NAME="llama-server"; CLI_LLM_HEALTH_URL="http://127.0.0.1:8080/health"; CLI_LLM_MODE=local; }
get_compose_flags() { :; }
get_apple_silicon_info() { APPLE_CHIP="Apple M4"; }
get_system_ram_gb() { SYSTEM_RAM_GB=32; }
get_native_llama_status() { NATIVE_LLAMA_RUNNING=false; }
webui_is_selected() { return 1; }
docker() { :; }
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_err() { :; }
curl_with_bearer() { printf '%s\n' "${*: -1}" >> "$TMP_DIR/status-urls"; printf '200'; }

check_port() {
    local env_line="$1" expected="$2"
    printf '%s\n' "$env_line" > "$INSTALL_DIR/.env"
    : > "$TMP_DIR/status-urls"
    run_readiness
    grep -qx "Dashboard|http://127.0.0.1:${expected}|ods-dashboard|http://localhost:${expected}" "$TMP_DIR/rows" \
        || fail "'$env_line': readiness row is '$(grep '^Dashboard|' "$TMP_DIR/rows")'"
    [[ "$(cat "$TMP_DIR/summary-url")" == "http://localhost:${expected}" ]] \
        || fail "'$env_line': readiness summary links $(cat "$TMP_DIR/summary-url")"
    (cmd_status >/dev/null)
    grep -qx "http://127.0.0.1:${expected}" "$TMP_DIR/status-urls" \
        || fail "'$env_line': status probed $(tr '\n' ' ' < "$TMP_DIR/status-urls")"
}

check_port 'DASHBOARD_PORT=3101' 3101
check_port 'DASHBOARD_PORT="3102"' 3102
check_port 'WEBUI_PORT=3000' 3001
check_port 'DASHBOARD_PORT=not-a-port' 3001

echo "[PASS] macOS readiness, success card and status use the configured Dashboard port"
