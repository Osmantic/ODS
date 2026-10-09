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
if [[ "${1:-}" == ps ]]; then
    [[ "${FAKE_DOCKER_MODE:-}" == postcheck-fail ]] && exit 77
    [[ "${FAKE_DOCKER_MODE:-}" == project-survives ]] && printf '%s\n' 'unselected-running-service'
    exit 0
fi
if [[ "${1:-}" == "compose" && "${2:-}" == "ls" ]]; then
    [[ "${FAKE_DOCKER_MODE:-}" == inventory-fail ]] && exit 73
    printf '%s\n' "ods"
    exit 0
fi
if [[ "$*" == *"config --format json"* ]]; then
    [[ "${FAKE_DOCKER_MODE:-}" == config-fail ]] && exit 74
    printf '%s\n' '{"name":"ods"}'
fi
[[ "${FAKE_DOCKER_MODE:-}" == stop-fail && "$*" == *" down" ]] && exit 75
exit 0
EOF
    chmod +x "$stub_dir/docker"
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/scripts" "$install_dir/data" "$install_dir/lib" "$install_dir/.backups/keepme"
    cp "$TARGET" "$install_dir/ods-restore.sh"
    cp -a "$ROOT_DIR/bin" "$install_dir/bin"
    mkdir -p "$install_dir/extensions/services/dashboard-api"
    cp "$ROOT_DIR/extensions/services/dashboard-api/"*.py "$install_dir/extensions/services/dashboard-api/"
    cp "$ROOT_DIR/scripts/backup-native-preflight.py" "$ROOT_DIR/scripts/source-update-preflight.py" "$install_dir/scripts/"
    cp "$ROOT_DIR/lib/rsync.sh" "$install_dir/lib/rsync.sh"
    cp "$ROOT_DIR/lib/backup-paths.sh" "$install_dir/lib/backup-paths.sh"
    cp "$ROOT_DIR/lib/backup-archive.py" "$install_dir/lib/backup-archive.py"
    touch "$install_dir/docker-compose.base.yml"
    touch "$install_dir/docker-compose.cpu.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
    printf '%s\n' '{"manifest_version": "1.0", "native_scope": "native-not-configured", "backup_date": "2026-09-18T00:00:00Z"}' \
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
    if grep -F -- '--remove-orphans' "$docker_log"; then
        fail "restore must not widen shutdown to orphan containers"
    fi
    pass "restore stops the authoritative Compose project with saved .compose-flags"

    local spaced="$TMP_DIR/install-spaced" spaced_log="$TMP_DIR/spaced.log"
    make_install "$spaced"
    touch "$spaced/overlay space.yml"
    printf "%s\n" "-f docker-compose.base.yml -f 'overlay space.yml'" > "$spaced/.compose-flags"
    : > "$spaced_log"
    DOCKER_LOG="$spaced_log" run_restore "$spaced" "$stub_dir"
    grep -qF 'compose -f docker-compose.base.yml -f overlay space.yml down' "$spaced_log" || fail "quoted paths lost"
    pass "quoted Compose file paths survive parsing"

    local mode failed_install failed_log
    for mode in empty-flags malformed-flags missing-file missing-stack missing-receipt-with-resolver config-fail inventory-fail stop-fail project-survives postcheck-fail; do
        failed_install="$TMP_DIR/failure-$mode"
        failed_log="$TMP_DIR/$mode.log"
        make_install "$failed_install"
        printf 'recovery sentinel\n' > "$failed_install/data/sentinel"
        case "$mode" in
            empty-flags) : > "$failed_install/.compose-flags" ;;
            malformed-flags) printf '%s\n' '--project-name foreign' > "$failed_install/.compose-flags" ;;
            missing-file) printf '%s\n' '-f missing.yml' > "$failed_install/.compose-flags" ;;
            missing-stack) rm "$failed_install/.compose-flags" ;;
            missing-receipt-with-resolver)
                rm "$failed_install/.compose-flags"
                printf '#!/bin/sh\nprintf "%%s\\n" "-f docker-compose.base.yml"\n' > "$failed_install/scripts/resolve-compose-stack.sh"
                chmod +x "$failed_install/scripts/resolve-compose-stack.sh" ;;
        esac
        : > "$failed_log"
        if FAKE_DOCKER_MODE="$mode" DOCKER_LOG="$failed_log" run_restore "$failed_install" "$stub_dir" 2>/dev/null; then
            fail "$mode must refuse restore"
        fi
        [[ "$(cat "$failed_install/data/sentinel")" == 'recovery sentinel' ]] || fail "$mode lost recovery data"
        if [[ "$mode" != stop-fail && "$mode" != project-survives && "$mode" != postcheck-fail ]] && grep -F ' down' "$failed_log"; then fail "$mode attempted shutdown"; fi
        pass "$mode fails closed before restoring data"
    done
}

main "$@"
