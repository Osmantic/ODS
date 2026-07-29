#!/usr/bin/env bash
# Regression checks for macOS LaunchAgent cleanup during uninstall (#1882).
# Hermetic: stubs uname/launchctl/docker/systemctl, fake HOME — no real
# macOS mutation, runs on Linux CI.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/ods-uninstall.sh"
TMP_DIR=""

CURRENT_LABELS="com.ods.host-agent com.ods.opencode-web"
LEGACY_LABELS="com.ods.llama-server com.ods.full-model-download"

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
    cat > "$stub_dir/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-enabled" ]]; then
    exit 1
fi
exit 0
EOF
    cat > "$stub_dir/sudo" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    cat > "$stub_dir/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    cat > "$stub_dir/uname" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-s" || $# -eq 0 ]]; then
    echo "${UNAME_S:-Darwin}"
    exit 0
fi
echo "${UNAME_S:-Darwin}"
EOF
    # launchctl stub:
    #  - logs every invocation to LAUNCHCTL_LOG
    #  - `print gui/<uid>/<label>` succeeds only for labels in LOADED_LABELS
    #  - `bootout gui/<uid>/<label>` fails for labels in BOOTOUT_FAIL
    cat > "$stub_dir/launchctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${LAUNCHCTL_LOG:?}"
label="${2##*/}"
case "${1:-}" in
    print)
        [[ " ${LOADED_LABELS:-} " == *" $label "* ]] && exit 0
        exit 113
        ;;
    bootout)
        [[ " ${BOOTOUT_FAIL:-} " == *" $label "* ]] && exit 5
        exit 0
        ;;
esac
exit 0
EOF
    chmod +x "$stub_dir"/*
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/lib"
    cp "$TARGET" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/safe-env.sh"
    touch "$install_dir/ods-cli"
}

make_home_with_plists() {
    local home_dir="$1"
    shift
    mkdir -p "$home_dir/Library/LaunchAgents"
    local label
    for label in "$@"; do
        printf '<plist/>\n' > "$home_dir/Library/LaunchAgents/${label}.plist"
    done
}

run_uninstall() {
    local install_dir="$1"
    local home_dir="$2"
    local stub_dir="$3"
    local out_file="$4"

    HOME="$home_dir" \
    INSTALL_DIR="$install_dir" \
    PATH="$stub_dir:$PATH" \
    LAUNCHCTL_LOG="${LAUNCHCTL_LOG:?}" \
    UNAME_S="${UNAME_S:-Darwin}" \
    LOADED_LABELS="${LOADED_LABELS:-}" \
    BOOTOUT_FAIL="${BOOTOUT_FAIL:-}" \
        bash "$install_dir/ods-uninstall.sh" --force > "$out_file" 2>&1
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"

    TMP_DIR="$(mktemp -d -t ods-launchagent-test-XXXXXX)"
    trap 'chmod -R u+w "$TMP_DIR" 2>/dev/null; rm -rf "$TMP_DIR"' EXIT

    local stub_dir="$TMP_DIR/bin"
    mkdir -p "$stub_dir"
    make_stub_bin "$stub_dir"
    local uid
    uid="$(id -u)"

    # ── Scenario 1: normal macOS uninstall boots out and removes all agents ──
    local install1="$TMP_DIR/install1" home1="$TMP_DIR/home1"
    make_install "$install1"
    # shellcheck disable=SC2086
    make_home_with_plists "$home1" $CURRENT_LABELS $LEGACY_LABELS
    LAUNCHCTL_LOG="$TMP_DIR/launchctl1.log" LOADED_LABELS="$CURRENT_LABELS" \
        run_uninstall "$install1" "$home1" "$stub_dir" "$TMP_DIR/out1.log" \
        || fail "normal macOS uninstall exited non-zero"

    local label
    for label in $CURRENT_LABELS; do
        grep -qF "bootout gui/$uid/$label" "$TMP_DIR/launchctl1.log" \
            || fail "uninstall must bootout $label"
    done
    for label in $CURRENT_LABELS $LEGACY_LABELS; do
        [[ ! -f "$home1/Library/LaunchAgents/${label}.plist" ]] \
            || fail "uninstall must remove ${label}.plist"
    done
    pass "macOS uninstall boots out loaded agents and removes all ODS plists (incl. legacy)"

    # ── Scenario 2: nothing installed — tolerated, no bootout, no warnings ──
    local install2="$TMP_DIR/install2" home2="$TMP_DIR/home2"
    make_install "$install2"
    mkdir -p "$home2/Library/LaunchAgents"
    LAUNCHCTL_LOG="$TMP_DIR/launchctl2.log" LOADED_LABELS="" \
        run_uninstall "$install2" "$home2" "$stub_dir" "$TMP_DIR/out2.log" \
        || fail "uninstall with no agents installed must still succeed"
    if grep -qF "bootout" "$TMP_DIR/launchctl2.log" 2>/dev/null; then
        fail "no bootout should be attempted when no agent is loaded"
    fi
    if grep -qiE "could not (boot out|remove).*com\.ods" "$TMP_DIR/out2.log"; then
        fail "missing agents must not produce cleanup warnings"
    fi
    pass "missing agents/plists are tolerated without warnings"

    # ── Scenario 3: bootout failure warns but does not fail uninstall ──
    local install3="$TMP_DIR/install3" home3="$TMP_DIR/home3"
    make_install "$install3"
    make_home_with_plists "$home3" com.ods.host-agent
    LAUNCHCTL_LOG="$TMP_DIR/launchctl3.log" LOADED_LABELS="com.ods.host-agent" \
    BOOTOUT_FAIL="com.ods.host-agent" \
        run_uninstall "$install3" "$home3" "$stub_dir" "$TMP_DIR/out3.log" \
        || fail "bootout failure must not fail the uninstall"
    grep -qiE "could not boot out.*com\.ods\.host-agent" "$TMP_DIR/out3.log" \
        || fail "failed bootout must produce a warning"
    pass "failed bootout warns without aborting uninstall"

    # ── Scenario 4: plist removal failure warns but does not fail uninstall ──
    local install4="$TMP_DIR/install4" home4="$TMP_DIR/home4"
    make_install "$install4"
    make_home_with_plists "$home4" com.ods.opencode-web
    chmod 555 "$home4/Library/LaunchAgents"
    LAUNCHCTL_LOG="$TMP_DIR/launchctl4.log" LOADED_LABELS="" \
        run_uninstall "$install4" "$home4" "$stub_dir" "$TMP_DIR/out4.log" \
        || fail "plist removal failure must not fail the uninstall"
    chmod 755 "$home4/Library/LaunchAgents"
    grep -qiE "could not remove.*com\.ods\.opencode-web" "$TMP_DIR/out4.log" \
        || fail "failed plist removal must produce a warning"
    pass "failed plist removal warns without aborting uninstall"

    # ── Scenario 5: Linux untouched — no launchctl calls at all ──
    local install5="$TMP_DIR/install5" home5="$TMP_DIR/home5"
    make_install "$install5"
    mkdir -p "$home5"
    LAUNCHCTL_LOG="$TMP_DIR/launchctl5.log" UNAME_S="Linux" \
        run_uninstall "$install5" "$home5" "$stub_dir" "$TMP_DIR/out5.log" \
        || fail "Linux uninstall must still succeed"
    [[ ! -s "$TMP_DIR/launchctl5.log" ]] \
        || fail "Linux uninstall must never invoke launchctl"
    pass "Linux uninstall path never touches launchctl"
}

main "$@"
