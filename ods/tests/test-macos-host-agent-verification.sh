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
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
TARGET="$ROOT_DIR/installers/macos/lib/host-agent-install.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"
grep -qF 'source "${LIB_DIR}/host-agent-install.sh"' "$INSTALLER" \
    || fail "installer must load the shared host-agent setup"
grep -qxF 'ods_macos_install_host_agent' "$INSTALLER" \
    || fail "installer must call the shared host-agent setup"

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
grep -qF "printf 'Authorization: Bearer %s\\n' \"\$api_key\"" <<<"$verify_block" \
    && grep -qF 'docker exec -i ods-dashboard-api' <<<"$verify_block" \
    && grep -qF -- '-H @-' <<<"$verify_block" \
    || fail "dashboard container readiness must send ODS_AGENT_KEY on stdin, never in argv"
grep -qF '/v1/model/status"' <<<"$verify_block" \
    || fail "dashboard container readiness must call the authenticated host-agent status endpoint"
pass "dashboard container verifies authenticated host-agent reachability"

# Exercise the shared setup without touching the real login services or ports.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
(
    export HOME="$TMP/home"
    INSTALL_DIR="$TMP/install"
    ODS_LOG_FILE="$TMP/install.log"
    ODS_AGENT_PLIST_LABEL="com.ods.host-agent"
    ODS_AGENT_PLIST="$HOME/Library/LaunchAgents/$ODS_AGENT_PLIST_LABEL.plist"
    calls="$TMP/calls"
    mkdir -p "$INSTALL_DIR/bin"
    touch "$INSTALL_DIR/bin/ods-host-agent.py"
    source "$TARGET"
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_err() { :; }
    sleep() { :; }
    _compute_launchd_path() { printf '/usr/bin:/bin\n'; }
    read_env_value() {
        case "$2" in
            ODS_AGENT_BIND) printf '127.0.0.1\n' ;;
            ODS_AGENT_PORT) printf '7710\n' ;;
            *) return 1 ;;
        esac
    }
    macos_normalize_agent_bind() { printf '%s\n' "$1"; }
    macos_bind_probe_host() { printf '%s\n' "$1"; }
    _ensure_macos_agent_python() {
        printf 'runtime\n' >> "$calls"
        [[ "$mode" != runtime-fails ]] || return 1
        AGENT_PYTHON="$INSTALL_DIR/.venv/host-agent/bin/python"
    }
    macos_retire_owned_host_agent_listener() {
        printf 'retire-owned\n' >> "$calls"
        [[ "$mode" != foreign-listener ]]
    }
    docker() { fail "host-agent setup unexpectedly ran Docker"; }
    launchctl() {
        printf '%s\n' "$1" >> "$calls"
        [[ "$1" != bootstrap || "$mode" != bootstrap-fails ]]
    }
    curl() {
        [[ "$*" == *'http://127.0.0.1:7710/health'* ]] || return 2
        printf 'health\n' >> "$calls"
        [[ "$mode" != health-fails && "$mode" != slow-start ]]
    }
    _configure_macos_host_agent_bridge() {
        printf 'bridge\n' >> "$calls"
        [[ "$mode" != bridge-fails ]]
    }
    _verify_macos_dashboard_host_agent() {
        [[ "$1" == "$INSTALL_DIR/.env" ]] || return 2
        printf 'authenticated-proof\n' >> "$calls"
        [[ "$mode" != health-fails && "$mode" != auth-fails ]]
    }
    mode=ok
    : > "$calls"
    ods_macos_install_host_agent || fail "healthy host-agent setup failed"
    [[ "$(cat "$calls")" == $'runtime\nbootout\nretire-owned\nbootstrap\nkickstart\nhealth\nbridge\nauthenticated-proof' ]] \
        || fail "host-agent setup order changed"
    python3 - "$ODS_AGENT_PLIST" "$INSTALL_DIR" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as stream:
    value = plistlib.load(stream)
assert value["ProgramArguments"] == [
    sys.argv[2] + "/.venv/host-agent/bin/python",
    sys.argv[2] + "/bin/ods-host-agent.py",
    "--install-dir", sys.argv[2],
]
assert value["RunAtLoad"] is True
assert value["KeepAlive"] == {"SuccessfulExit": False}
PY
    for mode in runtime-fails foreign-listener bootstrap-fails health-fails bridge-fails auth-fails; do
        : > "$calls"
        if ods_macos_install_host_agent; then
            fail "$mode incorrectly reported successful host-agent setup"
        fi
        case "$mode" in
            runtime-fails)
                [[ "$(cat "$calls")" == runtime ]] || fail "failed runtime touched launchd" ;;
            foreign-listener)
                ! grep -qx bootstrap "$calls" || fail "foreign listener was replaced" ;;
            bootstrap-fails)
                ! grep -qx health "$calls" || fail "failed bootstrap reached health probe" ;;
            health-fails)
                [[ "$(grep -cx health "$calls")" -eq 10 ]] || fail "health retry bound changed" ;;
            bridge-fails)
                ! grep -qx authenticated-proof "$calls" || fail "failed bridge reached authentication" ;;
            auth-fails)
                grep -qx authenticated-proof "$calls" || fail "missing authenticated proof" ;;
        esac
    done
    mode=slow-start
    : > "$calls"
    ods_macos_install_host_agent || fail "setup removed authenticated probe's startup grace"
    [[ "$(grep -cx health "$calls")" -eq 10 ]] || fail "slow startup skipped initial health wait"
    grep -qx authenticated-proof "$calls" || fail "slow startup omitted authenticated proof"
    mode=ok
    rm "$INSTALL_DIR/bin/ods-host-agent.py"
    : > "$calls"
    if ods_macos_install_host_agent; then
        fail "missing host-agent script incorrectly passed setup"
    fi
    [[ ! -s "$calls" ]] || fail "missing script reached runtime or launchd"
)
pass "shared setup validates launchd configuration and propagates operational failures"

echo "[OK] macOS installer verifies ods-host-agent is responding before declaring success"
