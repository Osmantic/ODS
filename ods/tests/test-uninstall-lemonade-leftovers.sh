#!/usr/bin/env bash
# ods-uninstall.sh removes the image an upgraded AMD install built for the
# retired Lemonade runtime, only when that installation's Lemonade migration
# left data/lemonade-retired-volumes.json. Docker and the volume custody
# helper are stubs here: tests/test_uninstall_compose_volumes.py covers the
# retired volumes themselves.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d -t ods-uninstall-lemonade-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
FAILURES=0

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

STUB_DIR="$TMP_DIR/bin"
mkdir -p "$STUB_DIR"
cat > "$STUB_DIR/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"
case "${1:-} ${2:-}" in
    "image inspect") exit "${DOCKER_IMAGE_INSPECT_EXIT:-0}" ;;
    "image rm") exit "${DOCKER_IMAGE_RM_EXIT:-0}" ;;
esac
exit 0
EOF
cat > "$STUB_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" != "is-enabled" ]]
EOF
cat > "$STUB_DIR/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SUDO_LOG:?}"
exit 0
EOF
cat > "$STUB_DIR/id" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -u|-g) printf '1000\n' ;;
    -un) printf 'fixture-owner\n' ;;
    *) exit 1 ;;
esac
EOF
cat > "$STUB_DIR/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
# A native kernel, so the WSL startup retirement stays out of this fixture.
cat > "$STUB_DIR/uname" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-r" ]]; then
    printf 'fixture-native-kernel\n'
else
    /usr/bin/uname "$@"
fi
EOF
chmod +x "$STUB_DIR"/*

make_install() {
    local install_dir="$1"
    mkdir -p "$install_dir/data" "$install_dir/lib" "$install_dir/scripts" "$install_dir/systemd"
    cp "$ROOT_DIR/ods-uninstall.sh" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$ROOT_DIR/lib/system-uninstall.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/scripts/compose-cache-policy.py" "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        "$install_dir/scripts/"
    printf 'import sys\nsys.exit(0)\n' > "$install_dir/scripts/uninstall-compose-volumes.py"
    touch "$install_dir/ods-cli" "$install_dir/docker-compose.base.yml" "$install_dir/docker-compose.amd.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.amd.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=amd' > "$install_dir/.env"
}

write_record() {
    printf '{"installDir": "%s", "schemaVersion": 1, "volumeKeys": ["lemonade-cache", "lemonade-llama", "lemonade-recipe"]}\n' \
        "$1" > "$1/data/lemonade-retired-volumes.json"
}

# run_case NAME [uninstall options...]; leaves $TMP_DIR/NAME.{docker,out,rc}
run_case() {
    local name="$1" rc=0
    shift
    mkdir -p "$TMP_DIR/home-$name"
    HOME="$TMP_DIR/home-$name" \
    INSTALL_DIR="$TMP_DIR/$name" \
    PATH="$STUB_DIR:$PATH" \
    DOCKER_LOG="$TMP_DIR/$name.docker" \
    SUDO_LOG="$TMP_DIR/$name.sudo" \
    ODS_UNINSTALL_SYSTEMD_DIR="$TMP_DIR/$name/systemd" \
        bash "$TMP_DIR/$name/ods-uninstall.sh" --force "$@" >"$TMP_DIR/$name.out" 2>&1 || rc=$?
    touch "$TMP_DIR/$name.docker"
    printf '%s\n' "$rc" > "$TMP_DIR/$name.rc"
}

case_rc() { cat "$TMP_DIR/$1.rc"; }

# 1. A purge of a migrated AMD install removes the image without forcing it.
make_install "$TMP_DIR/purge"
write_record "$TMP_DIR/purge"
run_case purge
if [[ "$(case_rc purge)" == 0 ]] \
    && grep -qx 'image rm ods-lemonade-server:latest' "$TMP_DIR/purge.docker" \
    && grep -qF 'Removed the retired Lemonade image ods-lemonade-server:latest' "$TMP_DIR/purge.out"; then
    pass "purge removes the retired Lemonade image"
else
    fail "purge must remove ods-lemonade-server:latest (rc $(case_rc purge))"
fi
if grep -Eq '^image rm .*(-f|--force)' "$TMP_DIR/purge.docker"; then
    fail "the retired image must not be force-removed; Docker must keep an image a container uses"
else
    pass "the retired image is removed without --force"
fi

# 2. --keep-data keeps volumes and data, but the image is not user data.
make_install "$TMP_DIR/keep"
write_record "$TMP_DIR/keep"
run_case keep --keep-data
if [[ "$(case_rc keep)" == 0 ]] && grep -qx 'image rm ods-lemonade-server:latest' "$TMP_DIR/keep.docker" \
    && [[ -f "$TMP_DIR/keep/data/lemonade-retired-volumes.json" ]]; then
    pass "--keep-data also removes the retired image and keeps the record with the data"
else
    fail "--keep-data must remove the retired image and keep data/ (rc $(case_rc keep))"
fi

# 3. Without the migration record the image belongs to no proven installation.
make_install "$TMP_DIR/unrecorded"
run_case unrecorded
if [[ "$(case_rc unrecorded)" == 0 ]] && ! grep -q '^image ' "$TMP_DIR/unrecorded.docker"; then
    pass "an install without the Lemonade record leaves Docker images alone"
else
    fail "an install without the Lemonade record must not inspect or remove images"
fi

# 4. A symlinked record is not a record.
make_install "$TMP_DIR/linked"
mkdir -p "$TMP_DIR/record-source/data"
write_record "$TMP_DIR/record-source"
ln -s "$TMP_DIR/record-source/data/lemonade-retired-volumes.json" "$TMP_DIR/linked/data/lemonade-retired-volumes.json"
if [[ ! -L "$TMP_DIR/linked/data/lemonade-retired-volumes.json" ]]; then
    # Git Bash on Windows copies instead of linking unless native symlinks are enabled.
    echo "[SKIP] this platform's ln -s does not create symlinks"
else
    run_case linked
    if [[ "$(case_rc linked)" == 0 ]] && ! grep -q '^image ' "$TMP_DIR/linked.docker"; then
        pass "a symlinked record does not authorize image removal"
    else
        fail "a symlinked record must not authorize image removal"
    fi
fi

# 5. A missing image is the usual case: inspect only.
make_install "$TMP_DIR/absent"
write_record "$TMP_DIR/absent"
DOCKER_IMAGE_INSPECT_EXIT=1 run_case absent
if [[ "$(case_rc absent)" == 0 ]] && grep -qx 'image inspect ods-lemonade-server:latest' "$TMP_DIR/absent.docker" \
    && ! grep -q '^image rm' "$TMP_DIR/absent.docker"; then
    pass "a missing retired image is not removed"
else
    fail "a missing retired image must only be inspected"
fi

# 6. Docker refusing the removal (a container still uses it) is reported, not fatal.
make_install "$TMP_DIR/refused"
write_record "$TMP_DIR/refused"
DOCKER_IMAGE_RM_EXIT=1 run_case refused
if [[ "$(case_rc refused)" == 0 ]] && [[ ! -e "$TMP_DIR/refused" ]] \
    && grep -qF 'Could not remove the retired image ods-lemonade-server:latest (non-fatal)' "$TMP_DIR/refused.out" \
    && grep -qF 'docker image rm ods-lemonade-server:latest' "$TMP_DIR/refused.out"; then
    pass "a refused image removal is reported with the command and the uninstall completes"
else
    fail "a refused image removal must warn with the command and finish the uninstall (rc $(case_rc refused))"
fi

if [[ "$FAILURES" -gt 0 ]]; then
    for name in purge keep unrecorded linked absent refused; do
        [[ -f "$TMP_DIR/$name.out" ]] || continue
        echo "--- $name output" >&2
        tail -n 15 "$TMP_DIR/$name.out" >&2
    done
    echo "$FAILURES check(s) failed" >&2
    exit 1
fi
echo "All retired Lemonade image uninstall checks passed"
