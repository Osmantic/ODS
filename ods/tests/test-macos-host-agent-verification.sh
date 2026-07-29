#!/usr/bin/env bash
# Regression: after launchctl bootstrap succeeds for ods-host-agent, the
# macOS installer MUST:
#   1. force the spawn with `launchctl kickstart -p` (bootstrap alone can leave
#      the service "pended speculative" under launchd throttling), AND
#   2. poll /health on the configured bind before printing the [OK] line, and
#   3. prove the dashboard container reaches an authenticated endpoint.
#
# Without both, dashboard-api hits "Host agent unreachable" on every model and
# extension action even though the installer reports success (observed on an
# Apple Silicon fleet target during the 2026-05-23 fleet test).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/installers/macos/install-macos.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Pull the block that runs after `launchctl bootstrap ... ODS_AGENT_PLIST`
# returns rc 0 and ends at the matching `ai_ok "ODS host agent installed"`.
# Strip comments so descriptions cannot satisfy or fail the checks.
success_block="$(awk '
    /_agent_bootstrap_err=.*launchctl bootstrap.*ODS_AGENT_PLIST/ { in_block=1 }
    in_block { print }
    in_block && /^[[:space:]]*ai_warn "ODS host agent LaunchAgent failed/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

[[ -n "$success_block" ]] || fail "could not locate host-agent bootstrap success block"

plist_block="$(awk '
    /cat > "\$ODS_AGENT_PLIST"/ { in_block=1 }
    in_block { print }
    in_block && /^AGENT_PLIST_EOF/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF '<string>--install-dir</string>' <<<"$plist_block" \
    || fail "host-agent LaunchAgent must pass --install-dir explicitly"
grep -qF '<string>${INSTALL_DIR}</string>' <<<"$plist_block" \
    || fail "host-agent LaunchAgent must pass the installer-selected INSTALL_DIR"
pass "host-agent LaunchAgent passes explicit install directory"

grep -qE 'launchctl kickstart -p "gui/\$\(id -u\)/\$\{ODS_AGENT_PLIST_LABEL\}"' <<<"$success_block" \
    || fail "host-agent bootstrap success block must call \`launchctl kickstart -p\` to defeat launchd spawn-pending throttling"
pass "host-agent bootstrap kickstarts the service after bootstrap"

grep -qF '_agent_probe_host="$(macos_bind_probe_host "$_agent_native_bind")"' "$TARGET" \
    || fail "host-agent bootstrap must derive a reachable probe from ODS_AGENT_BIND"
grep -qF '"http://${_agent_probe_host}:${ODS_AGENT_PORT}/health"' <<<"$success_block" \
    || fail "host-agent bootstrap success block must poll /health on the configured bind before declaring [OK]"
pass "host-agent bootstrap polls its configured bind before declaring success"

grep -qE 'ai_warn[[:space:]]+"ODS host agent loaded but not responding' <<<"$success_block" \
    || fail "host-agent bootstrap success block must surface an ai_warn when the agent fails health-check (silent false success is the bug)"
pass "host-agent bootstrap warns on health-check timeout"

verify_block="$(awk '
    /^_verify_macos_dashboard_host_agent\(\)/ { found=1 }
    found { print }
    found && /^}/ { exit }
' "$TARGET")"
grep -qF 'container_state="$(docker inspect' <<<"$verify_block" \
    || fail "dashboard host-agent verification must first require a running dashboard-api container"
grep -qF '"ODS_AGENT_KEY"' <<<"$verify_block" \
    || fail "dashboard container readiness must read ODS_AGENT_KEY"
grep -qF -- '-H "Authorization: Bearer ${' <<<"$verify_block" \
    || fail "dashboard container readiness must authenticate with ODS_AGENT_KEY"
grep -qF '/v1/model/status"' <<<"$verify_block" \
    || fail "dashboard container readiness must call the authenticated host-agent status endpoint"
pass "dashboard container verifies authenticated host-agent reachability"

echo "[OK] macOS installer verifies ods-host-agent is responding before declaring success"
