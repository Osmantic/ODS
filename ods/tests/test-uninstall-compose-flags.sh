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

    # The discovery fallback feeds these listings straight into `docker rm -f`
    # and `docker volume rm`, so the stub reports unrelated names that merely
    # contain "ods" next to this project's own.
    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"

# Emit names the way `docker ps` / `docker volume ls` would, honouring
# `--filter name=<expr>` with Docker's own semantics: the expression is matched
# anywhere in the name, so an unanchored "ods" also matches "k3s_pods".
emit_filtered() {
    local expr="" arg name
    for arg in "$@"; do
        case "$arg" in
            name=*) expr="${arg#name=}" ;;
        esac
    done
    for name in $NAMES; do
        if [[ -z "$expr" || "$name" =~ $expr ]]; then
            printf '%s\n' "$name"
        fi
    done
}

if [[ "${1:-}" == "ps" ]]; then
    NAMES="ods-litellm ods-llama-server kube-pods-proxy methods-runner"
    emit_filtered "$@"
    exit 0
fi
if [[ "${1:-}" == "volume" && "${2:-}" == "ls" ]]; then
    NAMES="ods_perplexica-data ods-legacy-cache k3s_pods methods_cache"
    emit_filtered "$@"
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
    mkdir -p "$home_keep/.local/bin"
    ln -s "$install_keep/ods-cli" "$home_keep/.local/bin/ods"
    DOCKER_LOG="$log_keep" run_uninstall "$install_keep" "$home_keep" "$stub_dir" --keep-data

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml down --remove-orphans' "$log_keep" \
        || fail "uninstall must use saved .compose-flags for docker compose down"
    if grep -qF 'down -v --remove-orphans' "$log_keep"; then
        fail "--keep-data must not remove compose volumes with -v"
    fi
    pass "uninstall uses saved compose flags and preserves volumes with --keep-data"
    [[ ! -L "$home_keep/.local/bin/ods" ]] \
        || fail "uninstall must remove the user-level ods CLI symlink"
    pass "uninstall removes user-level ods CLI symlink"

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

    # Docker's `--filter name=` matches anywhere in the name, so the discovery
    # fallback must select on the project prefix (containers ods-<service>,
    # compose volumes ods_<volume>) and leave unrelated names alone.
    local removed_containers removed_volumes
    removed_containers="$(grep -E '^rm -f ' "$log_purge" || true)"
    removed_volumes="$(grep -E '^volume rm ' "$log_purge" || true)"

    local name
    for name in ods-litellm ods-llama-server; do
        [[ "$removed_containers" == *"$name"* ]] \
            || fail "uninstall must remove project container $name (got: '$removed_containers')"
    done
    for name in kube-pods-proxy methods-runner; do
        [[ "$removed_containers" != *"$name"* ]] \
            || fail "uninstall must not remove unrelated container $name (got: '$removed_containers')"
    done
    pass "container discovery stays on the ods- prefix"

    for name in ods_perplexica-data ods-legacy-cache; do
        [[ "$removed_volumes" == *"$name"* ]] \
            || fail "uninstall must remove project volume $name (got: '$removed_volumes')"
    done
    for name in k3s_pods methods_cache; do
        [[ "$removed_volumes" != *"$name"* ]] \
            || fail "uninstall must not remove unrelated volume $name (got: '$removed_volumes')"
    done
    pass "volume discovery stays on the ods project prefix"

    # 07-devtools.sh writes opencode.json from a template and syncs a config.json
    # copy (the document OpenCode actually reads). Uninstall must remove both
    # ODS-authored documents but leave an unrelated user config untouched.
    local install_oc="$TMP_DIR/install-oc"
    local home_oc="$TMP_DIR/home-oc"
    mkdir -p "$home_oc"
    make_install "$install_oc"
    mkdir -p "$home_oc/.config/opencode"
    cat > "$home_oc/.config/opencode/opencode.json" <<'EOF'
{ "$schema": "https://opencode.ai/config.json",
  "model": "llama-server/ods/current",
  "provider": { "llama-server": {} } }
EOF
    cp "$home_oc/.config/opencode/opencode.json" "$home_oc/.config/opencode/config.json"
    cat > "$home_oc/.config/opencode/user.json" <<'EOF'
{ "theme": "dark" }
EOF
    DOCKER_LOG="$TMP_DIR/docker-oc.log" run_uninstall "$install_oc" "$home_oc" "$stub_dir"

    [[ ! -f "$home_oc/.config/opencode/opencode.json" ]] \
        || fail "uninstall must remove ODS-managed opencode.json"
    [[ ! -f "$home_oc/.config/opencode/config.json" ]] \
        || fail "uninstall must remove the synced config.json document"
    [[ -f "$home_oc/.config/opencode/user.json" ]] \
        || fail "uninstall must not remove unrelated user opencode config"
    pass "uninstall surgically removes ODS OpenCode config but keeps user config"
}

main "$@"
