#!/usr/bin/env bash
# Regression checks for ODS uninstall compose cleanup.

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

    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"
if [[ "${1:-}" == "ps" ]]; then
    exit 0
fi
if [[ "${1:-}" == "volume" && "${2:-}" == "ls" ]]; then
    exit 0
fi
exit 0
EOF
    chmod +x "$stub_dir/docker"

    cat > "$stub_dir/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-enabled" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$stub_dir/systemctl"

    cat > "$stub_dir/sudo" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stub_dir/sudo"

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
    touch "$install_dir/docker-compose.cpu.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
}

run_uninstall() {
    local install_dir="$1"
    local home_dir="$2"
    local stub_dir="$3"
    shift 3

    HOME="$home_dir" \
    INSTALL_DIR="$install_dir" \
    PATH="$stub_dir:$PATH" \
    DOCKER_LOG="${DOCKER_LOG:?}" \
        bash "$install_dir/ods-uninstall.sh" --force "$@" >/dev/null
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"
    if grep -qF 'source "$INSTALL_DIR/.env"' "$TARGET"; then
        fail "uninstall must load .env through lib/safe-env.sh, not source it"
    fi

    TMP_DIR="$(mktemp -d -t ods-uninstall-test-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    local stub_dir="$TMP_DIR/bin"
    mkdir -p "$stub_dir"
    make_stub_bin "$stub_dir"

    local install_keep="$TMP_DIR/install-keep"
    local home_keep="$TMP_DIR/home-keep"
    local log_keep="$TMP_DIR/docker-keep.log"
    mkdir -p "$home_keep"
    make_install "$install_keep"
    DOCKER_LOG="$log_keep" run_uninstall "$install_keep" "$home_keep" "$stub_dir" --keep-data

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml down --remove-orphans' "$log_keep" \
        || fail "uninstall must use saved .compose-flags for docker compose down"
    if grep -qF 'down -v --remove-orphans' "$log_keep"; then
        fail "--keep-data must not remove compose volumes with -v"
    fi
    pass "uninstall uses saved compose flags and preserves volumes with --keep-data"

    local install_purge="$TMP_DIR/install-purge"
    local home_purge="$TMP_DIR/home-purge"
    local log_purge="$TMP_DIR/docker-purge.log"
    mkdir -p "$home_purge"
    make_install "$install_purge"
    DOCKER_LOG="$log_purge" run_uninstall "$install_purge" "$home_purge" "$stub_dir"

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml down -v --remove-orphans' "$log_purge" \
        || fail "normal uninstall must remove compose volumes with -v"
    pass "normal uninstall removes compose volumes"

    local install_safe="$TMP_DIR/install-safe-env"
    local home_safe="$TMP_DIR/home-safe-env"
    local log_safe="$TMP_DIR/docker-safe-env.log"
    mkdir -p "$home_safe"
    make_install "$install_safe"
    cat > "$install_safe/.env" <<'EOF'
GPU_BACKEND=$(touch "$HOME/uninstall-env-sourced")
EOF
    DOCKER_LOG="$log_safe" run_uninstall "$install_safe" "$home_safe" "$stub_dir" --keep-data

    if [[ -e "$home_safe/uninstall-env-sourced" ]]; then
        fail "uninstall must not execute command substitutions from .env"
    fi
    pass "uninstall loads .env without executing shell substitutions"
}

main "$@"
