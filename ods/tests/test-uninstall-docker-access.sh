#!/usr/bin/env bash
# The uninstaller must not delete an installation whose containers it cannot
# remove. Before this check, a refused Docker socket (user outside the docker
# group, which the installer supports through sudo) or a stopped daemon made
# every cleanup call fail silently: it logged "Docker cleanup complete",
# deleted the install tree and left the whole stack running, or restarting
# with Docker, against paths that no longer existed.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/ods-uninstall.sh"
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

    # DOCKER_MODE: ok, denied (socket refuses this user, sudo reaches it) or
    # down (no daemon). Containers live in $CONTAINERS so removals persist.
    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s|%s\n' "${VIA_SUDO:-0}" "$*" >> "${DOCKER_LOG:?}"
case "${DOCKER_MODE:?}" in
    down)
        echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2
        exit 1
        ;;
    denied)
        if [[ "${VIA_SUDO:-0}" != 1 ]]; then
            echo "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock" >&2
            exit 1
        fi
        ;;
esac
case "${1:-}" in
    ps)
        cat "${CONTAINERS:?}"
        ;;
    rm)
        shift
        [[ "${1:-}" == -f ]] && shift
        for name in "$@"; do
            [[ "$name" == "${RM_FAIL:-}" ]] && exit 1
            grep -vx -- "$name" "$CONTAINERS" > "$CONTAINERS.next" || true
            mv "$CONTAINERS.next" "$CONTAINERS"
        done
        ;;
    volume)
        [[ "${2:-}" == ls ]] && printf 'ods_qdrant-data\n'
        ;;
esac
exit 0
EOF
    chmod +x "$stub_dir/docker"

    # Run only docker for real under sudo; record everything else.
    cat > "$stub_dir/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SUDO_LOG:?}"
[[ "${1:-}" == -n ]] && shift
[[ "${1:-}" == -- ]] && shift
if [[ "${1:-}" == docker ]]; then
    VIA_SUDO=1 exec "$@"
fi
exit 0
EOF
    chmod +x "$stub_dir/sudo"

    cat > "$stub_dir/uname" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == -s ]] && { echo Linux; exit 0; }
exec /usr/bin/uname "$@"
EOF
    chmod +x "$stub_dir/uname"

    cat > "$stub_dir/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == "is-enabled" ]] && exit 1
exit 0
EOF
    chmod +x "$stub_dir/systemctl"

    cat > "$stub_dir/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$stub_dir/pgrep"
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/data" "$install_dir/lib" "$install_dir/systemd"
    cp "$TARGET" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/safe-env.sh"
    cp "$ROOT_DIR/lib/system-uninstall.sh" "$install_dir/lib/system-uninstall.sh"
    touch "$install_dir/ods-cli" "$install_dir/docker-compose.base.yml" "$install_dir/docker-compose.cpu.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
}

# run_scenario NAME MODE [RM_FAIL]; sets RC, INSTALL, OUT and CONTAINERS.
run_scenario() {
    local name="$1" mode="$2" rm_fail="${3:-}"
    INSTALL="$TMP_DIR/$name/ods"
    OUT="$TMP_DIR/$name/out.log"
    CONTAINERS="$TMP_DIR/$name/containers"
    DOCKER_LOG="$TMP_DIR/$name/docker.log"
    SUDO_LOG="$TMP_DIR/$name/sudo.log"
    mkdir -p "$TMP_DIR/$name/home"
    make_install "$INSTALL"
    printf '%s\n' ods-webui ods-litellm unrelated-db > "$CONTAINERS"
    : > "$DOCKER_LOG"
    : > "$SUDO_LOG"

    set +e
    HOME="$TMP_DIR/$name/home" \
    INSTALL_DIR="$INSTALL" \
    PATH="$TMP_DIR/bin:$PATH" \
    DOCKER_MODE="$mode" \
    RM_FAIL="$rm_fail" \
    CONTAINERS="$CONTAINERS" \
    DOCKER_LOG="$DOCKER_LOG" \
    SUDO_LOG="$SUDO_LOG" \
    ODS_UNINSTALL_SYSTEMD_DIR="$INSTALL/systemd" \
        bash "$INSTALL/ods-uninstall.sh" --force --non-interactive > "$OUT" 2>&1
    RC=$?
    set -e
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"
    TMP_DIR="$(mktemp -d -t ods-uninstall-docker-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    mkdir -p "$TMP_DIR/bin"
    make_stub_bin "$TMP_DIR/bin"

    run_scenario reachable ok
    [[ "$RC" -eq 0 ]] || { cat "$OUT" >&2; fail "uninstall with a reachable daemon exited $RC"; }
    [[ "$(cat "$CONTAINERS")" == unrelated-db ]] || fail "reachable daemon: ODS containers not removed"
    [[ ! -e "$INSTALL" ]] || fail "reachable daemon: installation not removed"
    ! grep -q '^1|' "$DOCKER_LOG" || fail "reachable daemon: docker must not run through sudo"
    pass "reachable daemon: containers and installation removed without sudo"

    run_scenario denied denied
    [[ "$RC" -eq 0 ]] || { cat "$OUT" >&2; fail "uninstall outside the docker group exited $RC"; }
    [[ "$(cat "$CONTAINERS")" == unrelated-db ]] \
        || fail "outside the docker group: ODS containers left running ($(tr '\n' ' ' < "$CONTAINERS"))"
    grep -q '^1|rm -f ods-webui$' "$DOCKER_LOG" || fail "outside the docker group: containers must be removed through sudo"
    [[ ! -e "$INSTALL" ]] || fail "outside the docker group: installation not removed"
    pass "outside the docker group: containers removed through sudo, then the installation"

    run_scenario down down
    [[ "$RC" -ne 0 ]] || fail "stopped daemon: uninstall must fail"
    [[ -f "$INSTALL/ods-cli" ]] || fail "stopped daemon: installation must be retained"
    [[ "$(wc -l < "$CONTAINERS" | tr -d ' ')" -eq 3 ]] || fail "stopped daemon: containers changed"
    grep -q 'Cannot reach the Docker daemon' "$OUT" || fail "stopped daemon: missing explanation"
    ! grep -q 'docker' "$SUDO_LOG" || fail "stopped daemon: must not escalate to sudo for an unreachable daemon"
    pass "stopped daemon: refuses before any change and keeps the installation"

    run_scenario stuck ok ods-litellm
    [[ "$RC" -ne 0 ]] || fail "surviving container: uninstall must fail"
    [[ -f "$INSTALL/ods-cli" ]] || fail "surviving container: installation must be retained"
    grep -q 'ODS containers are still present: ods-litellm' "$OUT" || fail "surviving container: missing its name"
    pass "surviving container: installation retained and the container named"
}

main "$@"
