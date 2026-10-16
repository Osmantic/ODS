#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINUX_ENV="$ROOT_DIR/installers/phases/06-directories.sh"
MACOS_ENV="$ROOT_DIR/installers/macos/lib/env-generator.sh"
WINDOWS_ENV="$ROOT_DIR/installers/windows/lib/env-generator.ps1"
WINDOWS_CLI="$ROOT_DIR/installers/windows/ods.ps1"
WINDOWS_OPENCODE="$ROOT_DIR/installers/windows/phases/07-devtools.ps1"
LINUX_OPENCODE="$ROOT_DIR/opencode/opencode-web.service"
LINUX_CLI="$ROOT_DIR/ods-cli"
MACOS_CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

require() {
    local pattern="$1" file="$2" message="$3"
    if ! grep -Eq -- "$pattern" "$file"; then
        printf 'FAIL: %s\n' "$message" >&2
        exit 1
    fi
}

# Prints the number of the first line of $2 that matches $1, or nothing when no
# line matches; require_before reports the missing match.
first_match_line() {
    local match
    match="$(grep -nE -m1 -- "$1" <<< "$2")" || return 0
    printf '%s\n' "${match%%:*}"
}

# Inside the named shell function, a line matching $first must come before
# the first line matching $second.
require_before() {
    local file="$1" function_name="$2" first="$3" second="$4" message="$5"
    local body first_line second_line
    body="$(sed -n "/^${function_name}() {/,/^}/p" "$file")"
    first_line="$(first_match_line "$first" "$body")"
    second_line="$(first_match_line "$second" "$body")"
    if [[ -z "$first_line" || -z "$second_line" || "$first_line" -ge "$second_line" ]]; then
        printf 'FAIL: %s\n' "$message" >&2
        exit 1
    fi
}

require '_env_get WEBUI_AUTH "false"' "$LINUX_ENV" \
    "Linux loopback installs must default Open WebUI auth off"
require 'WEBUI_AUTH="true"' "$LINUX_ENV" \
    "Linux network installs must force Open WebUI auth on"
require 'ENABLE_ODS_PROXY.*false' "$LINUX_ENV" \
    "Linux LAN proxy installs must keep Open WebUI auth on"

require '127\.0\.0\.1\|::1\|localhost.*webui_auth=.*false' "$MACOS_ENV" \
    "macOS loopback installs must default Open WebUI auth off"
require 'upsert_env_value.*WEBUI_AUTH.*true' "$MACOS_ENV" \
    "macOS network transitions must force Open WebUI auth on"

require 'networkExposed = ' "$WINDOWS_ENV" \
    "Windows loopback auth policy is missing"
require '127\.0\.0\.1", "::1", "localhost' "$WINDOWS_ENV" \
    "Windows loopback host allowlist is missing"
require 'EnableODSProxy' "$WINDOWS_ENV" \
    "Windows LAN proxy auth policy is missing"
require 'webuiAuth = "true"' "$WINDOWS_ENV" \
    "Windows network transitions must force Open WebUI auth on"

require '_ods_cli_require_proxy_auth' "$LINUX_CLI" \
    "Linux extension management must enforce proxy authentication"
require 'force-recreate open-webui' "$LINUX_CLI" \
    "Linux must apply authentication before starting the proxy"
require 'resolved_service.*open-webui' "$LINUX_CLI" \
    "Linux Open WebUI-only lifecycle commands must preserve proxy authentication"
require '_ods_cli_proxy_enabled \|\| _ods_cli_bind_is_network' "$LINUX_CLI" \
    "Linux must require Open WebUI sign-in for a network BIND_ADDRESS as well as the proxy"
require '&& _ods_cli_network_access_enabled; then' "$LINUX_CLI" \
    "Linux start and restart must enforce sign-in whenever Open WebUI is network-reachable"
require 'require_proxy_auth' "$MACOS_CLI" \
    "macOS service management must enforce proxy authentication"
require 'force-recreate open-webui' "$MACOS_CLI" \
    "macOS must apply authentication before starting the proxy"
require 'service.*open-webui.*network_access_is_enabled' "$MACOS_CLI" \
    "macOS Open WebUI-only lifecycle commands must preserve proxy authentication"
require 'proxy_is_enabled \|\| bind_is_network' "$MACOS_CLI" \
    "macOS must require Open WebUI sign-in for a network BIND_ADDRESS as well as the proxy"
# `update` recreates every container, Open WebUI included.
require_before "$LINUX_CLI" cmd_update 'if _ods_cli_network_access_enabled; then' 'up -d --force-recreate' \
    "Linux update must check network access before recreating Open WebUI"
require_before "$LINUX_CLI" cmd_update '^ +_ods_cli_require_proxy_auth$' 'up -d --force-recreate' \
    "Linux update must enforce sign-in before recreating Open WebUI"
require_before "$MACOS_CLI" cmd_update 'if network_access_is_enabled; then' 'up -d --force-recreate' \
    "macOS update must check network access before recreating Open WebUI"
require_before "$MACOS_CLI" cmd_update '^ +require_proxy_auth \|\| return 1$' 'up -d --force-recreate' \
    "macOS update must enforce sign-in before recreating Open WebUI"
require 'Set-ODSProxyAuthRequired' "$WINDOWS_CLI" \
    "Windows extension management must enforce proxy authentication"
require 'ODS_PROXY_AUTH_PREFLIGHT_FAILED' "$WINDOWS_CLI" \
    "Windows must fail closed when authenticated Open WebUI recreation fails"
require 'Service -eq "open-webui"' "$WINDOWS_CLI" \
    "Windows Open WebUI-only lifecycle commands must preserve proxy authentication"
# tests/test-windows-network-signin.ps1 runs the Windows start, restart and
# update paths themselves.

require 'UnsetEnvironment=OPENCODE_SERVER_PASSWORD' "$LINUX_OPENCODE" \
    "Linux OpenCode must clear inherited browser auth"
require 'Remove-Item Env:OPENCODE_SERVER_PASSWORD' "$WINDOWS_OPENCODE" \
    "Windows OpenCode must clear inherited browser auth"
require '--hostname 127\.0\.0\.1' "$WINDOWS_OPENCODE" \
    "Passwordless Windows OpenCode must remain loopback-only"

printf 'PASS: local app authentication defaults\n'
