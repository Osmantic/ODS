#!/usr/bin/env bash
# Regression check for ods-restore.sh container shutdown.
#
# The install tree has no top-level docker-compose.yml: the stack is
# docker-compose.base.yml plus GPU/extension overlays recorded in
# .compose-flags. A bare `docker compose down` finds no project file and
# fails, so --stop-containers restores must resolve the saved flags the
# same way ods-uninstall.sh does.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/ods-restore.sh"
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

    # The project name is the install dir basename; reporting it through
    # `compose ls` makes stop_containers attempt `compose down`.
    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"
if [[ "${1:-}" == "compose" && "${2:-}" == "ls" ]]; then
    printf '%s\n' "${FAKE_PROJECT:?}"
    exit 0
fi
exit 0
EOF
    chmod +x "$stub_dir/docker"
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/data" "$install_dir/lib" "$install_dir/.backups/keepme"
    cp "$TARGET" "$install_dir/ods-restore.sh"
    cp "$ROOT_DIR/lib/rsync.sh" "$install_dir/lib/rsync.sh"
    cp "$ROOT_DIR/lib/backup-paths.sh" "$install_dir/lib/backup-paths.sh"
    cp "$ROOT_DIR/lib/backup-archive.py" "$install_dir/lib/backup-archive.py"
    touch "$install_dir/docker-compose.base.yml"
    touch "$install_dir/docker-compose.cpu.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
    printf '%s\n' '{"manifest_version": "1.0", "backup_date": "2026-09-18T00:00:00Z"}' \
        > "$install_dir/.backups/keepme/manifest.json"
}

run_restore() {
    local install_dir="$1"
    local stub_dir="$2"
    shift 2

    ODS_DIR="$install_dir" \
    PATH="$stub_dir:$PATH" \
    DOCKER_LOG="${DOCKER_LOG:?}" \
    FAKE_PROJECT="$(basename "$install_dir")" \
        bash "$install_dir/ods-restore.sh" keepme --force --stop-containers --data-only "$@" >/dev/null
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"

    TMP_DIR="$(mktemp -d -t ods-restore-test-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    local stub_dir="$TMP_DIR/bin"
    mkdir -p "$stub_dir"
    make_stub_bin "$stub_dir"

    local install_dir="$TMP_DIR/install-ods"
    local docker_log="$TMP_DIR/docker.log"
    mkdir -p "$install_dir"
    : > "$docker_log"
    make_install "$install_dir"
    DOCKER_LOG="$docker_log" run_restore "$install_dir" "$stub_dir"

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml down' "$docker_log" \
        || fail "restore must stop containers through the saved .compose-flags stack (got: $(cat "$docker_log"))"
    pass "restore stops containers with saved .compose-flags"

    # Without the receipt, the resolver/base overlay fallback must still give
    # compose a project file instead of a bare `down`.
    local install_noflags="$TMP_DIR/install-noflags"
    local log_noflags="$TMP_DIR/docker-noflags.log"
    mkdir -p "$install_noflags"
    : > "$log_noflags"
    make_install "$install_noflags"
    rm -f "$install_noflags/.compose-flags"
    DOCKER_LOG="$log_noflags" run_restore "$install_noflags" "$stub_dir"

    grep -qF 'compose -f docker-compose.base.yml down' "$log_noflags" \
        || fail "restore must fall back to the base compose file when no receipt exists (got: $(cat "$log_noflags"))"
    pass "restore falls back to the base compose file without a receipt"
}

main "$@"
