#!/usr/bin/env bash
# Regression check: `ods-uninstall.sh` must disable and stop the system-mode
# ods-mdns.service unit that installers/phases/07-devtools.sh installs with
# `systemctl enable --now`, the same way it already handles ods-host-agent.
# Without this, uninstall leaves the mDNS announcer enabled against the
# deleted install directory.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_UNINSTALL_UNDER_TEST:-$ROOT_DIR/ods-uninstall.sh}"
TMP_DIR=""

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

make_stub_bin() {
    local stub_dir="$1"

    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stub_dir/docker"

    # `is-enabled <unit>` answers from ENABLED_UNITS (space-separated); every
    # invocation is logged so the test can see what the uninstaller asked for.
    cat > "$stub_dir/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SYSTEMCTL_LOG:?}"
if [[ "${1:-}" == "is-enabled" ]]; then
    for unit in ${ENABLED_UNITS:-}; do
        [[ "$unit" == "${2:-}" ]] && exit 0
    done
    exit 1
fi
exit 0
EOF
    chmod +x "$stub_dir/systemctl"

    # Privileged commands are logged, then executed so the systemctl stub
    # records what ran under sudo.
    cat > "$stub_dir/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SUDO_LOG:?}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v) exit 0 ;;
        -n) shift ;;
        --) shift; break ;;
        *) break ;;
    esac
done
[[ $# -gt 0 ]] || exit 0
exec "$@"
EOF
    chmod +x "$stub_dir/sudo"

    # coreutils `timeout DURATION CMD...` — drop the duration, run the command.
    cat > "$stub_dir/timeout" <<'EOF'
#!/usr/bin/env bash
shift
exec "$@"
EOF
    chmod +x "$stub_dir/timeout"

    cat > "$stub_dir/id" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -u|-g) printf '1000\n' ;;
    *) exit 1 ;;
esac
EOF
    chmod +x "$stub_dir/id"

    cat > "$stub_dir/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$stub_dir/pgrep"
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/data" "$install_dir/lib"
    cp "$TARGET" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/safe-env.sh"
    touch "$install_dir/ods-cli"
    touch "$install_dir/docker-compose.base.yml"
    printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
}

run_uninstall() {
    local install_dir="$1"
    local home_dir="$2"
    local stub_dir="$3"
    local enabled_units="$4"

    HOME="$home_dir" \
    INSTALL_DIR="$install_dir" \
    PATH="$stub_dir:$PATH" \
    ENABLED_UNITS="$enabled_units" \
    SYSTEMCTL_LOG="${SYSTEMCTL_LOG:?}" \
    SUDO_LOG="${SUDO_LOG:?}" \
        bash "$install_dir/ods-uninstall.sh" --force --keep-data >/dev/null
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"

    TMP_DIR="$(mktemp -d -t ods-uninstall-mdns-test-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    local stub_dir="$TMP_DIR/bin"
    mkdir -p "$stub_dir"
    make_stub_bin "$stub_dir"

    # Case 1: both system units are enabled (a Linux install where phase 07
    # set up the host agent and the mDNS announcer). Uninstall must disable
    # and stop each one through cached sudo credentials.
    local install_a="$TMP_DIR/install-a" home_a="$TMP_DIR/home-a"
    local systemctl_log_a="$TMP_DIR/systemctl-a.log" sudo_log_a="$TMP_DIR/sudo-a.log"
    : > "$systemctl_log_a"; : > "$sudo_log_a"
    mkdir -p "$home_a"
    make_install "$install_a"
    SYSTEMCTL_LOG="$systemctl_log_a" SUDO_LOG="$sudo_log_a" \
        run_uninstall "$install_a" "$home_a" "$stub_dir" "ods-host-agent.service ods-mdns.service"

    grep -qxF 'is-enabled ods-mdns.service' "$systemctl_log_a" \
        || fail "uninstall must check whether ods-mdns.service is enabled (systemctl calls: $(tr '\n' ';' < "$systemctl_log_a"))"
    grep -qxF -- '-n -- systemctl disable --now ods-mdns.service' "$sudo_log_a" \
        || fail "uninstall must disable and stop ods-mdns.service via sudo (sudo calls: $(tr '\n' ';' < "$sudo_log_a"))"
    grep -qxF -- '-n -- systemctl disable --now ods-host-agent.service' "$sudo_log_a" \
        || fail "uninstall must keep disabling ods-host-agent.service (sudo calls: $(tr '\n' ';' < "$sudo_log_a"))"
    pass "uninstall disables and stops ods-mdns.service alongside ods-host-agent.service"

    # Every privileged call after `sudo -v` must be non-interactive
    # (the contract tests/test-uninstall-compose-flags.sh enforces).
    local sudo_call credentials_seen=0
    while IFS= read -r sudo_call; do
        if [[ "$sudo_call" == "-v" ]]; then
            credentials_seen=1
            continue
        fi
        [[ "$credentials_seen" -eq 1 ]] \
            || fail "unit removal ran a privileged command before acquiring sudo credentials: $sudo_call"
        [[ "$sudo_call" == "-n -- "* ]] \
            || fail "unit removal must use cached credentials non-interactively: $sudo_call"
    done < "$sudo_log_a"
    pass "system unit removal uses cached sudo credentials non-interactively"

    # Case 2: neither unit is enabled (user-mode or macOS-style install).
    # Uninstall must stay idempotent and not try to disable anything.
    local install_b="$TMP_DIR/install-b" home_b="$TMP_DIR/home-b"
    local systemctl_log_b="$TMP_DIR/systemctl-b.log" sudo_log_b="$TMP_DIR/sudo-b.log"
    : > "$systemctl_log_b"; : > "$sudo_log_b"
    mkdir -p "$home_b"
    make_install "$install_b"
    SYSTEMCTL_LOG="$systemctl_log_b" SUDO_LOG="$sudo_log_b" \
        run_uninstall "$install_b" "$home_b" "$stub_dir" ""

    if grep -q 'disable' "$sudo_log_b"; then
        fail "uninstall must not disable system units that are not enabled (sudo calls: $(tr '\n' ';' < "$sudo_log_b"))"
    fi
    pass "uninstall skips system units that were never enabled"
}

main "$@"
