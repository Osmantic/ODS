#!/usr/bin/env bash
# `ods preset export` writes a tar.gz that contains the preset's copy of .env
# (DASHBOARD_API_KEY, service passwords, provider API keys). The archive was
# created at the umask default, 0644 or 0664, so any local user who could
# reach the file could read those secrets, although .env itself and the
# compressed backup archive are kept at 0600.
#
# Runs the real CLI against a throwaway install. No Docker is needed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

# make_install <dir>: the minimal install the CLI accepts (check_install + sr_load)
make_install() {
    local dir="$1"
    mkdir -p "$dir/lib" "$dir/extensions/services"
    cp "$ROOT_DIR/ods-cli" "$dir/ods-cli"
    cp "$ROOT_DIR"/lib/*.sh "$dir/lib/"
    : > "$dir/docker-compose.base.yml"
}

run_cli() {  # run_cli <install> <args...>
    local install="$1"
    shift
    ODS_HOME="$install" bash "$install/ods-cli" "$@"
}

umask 022
SOURCE="$TMP_DIR/source"
make_install "$SOURCE"
(umask 077 && printf 'GPU_BACKEND=nvidia\nANTHROPIC_API_KEY=sk-ant-fixture-secret\n' > "$SOURCE/.env")
run_cli "$SOURCE" preset save shared-setup > "$TMP_DIR/save.out" 2>&1 \
    || { cat "$TMP_DIR/save.out"; fail "preset save failed"; }

# ── New archive ──────────────────────────────────────────────────────────────
mkdir -p "$TMP_DIR/out"
if (cd "$TMP_DIR/out" && run_cli "$SOURCE" preset export shared-setup shared.tar.gz) > "$TMP_DIR/export.out" 2>&1; then
    mode="$(file_mode "$TMP_DIR/out/shared.tar.gz")"
    [[ "$mode" == 600 ]] \
        && pass "a new export archive is owner-only" \
        || fail "a new export archive has mode $mode under umask 022"
else
    cat "$TMP_DIR/export.out"
    fail "preset export failed"
fi

# ── Overwriting an existing world-readable file ──────────────────────────────
printf 'old contents\n' > "$TMP_DIR/out/existing.tar.gz"
chmod 644 "$TMP_DIR/out/existing.tar.gz"
if run_cli "$SOURCE" preset export shared-setup "$TMP_DIR/out/existing.tar.gz" > "$TMP_DIR/overwrite.out" 2>&1; then
    mode="$(file_mode "$TMP_DIR/out/existing.tar.gz")"
    [[ "$mode" == 600 ]] \
        && pass "overwriting an existing 0644 file leaves it owner-only" \
        || fail "overwriting an existing 0644 file left mode $mode"
else
    cat "$TMP_DIR/overwrite.out"
    fail "preset export over an existing file failed"
fi

# ── The archive still round-trips through import ─────────────────────────────
TARGET="$TMP_DIR/target"
make_install "$TARGET"
printf 'GPU_BACKEND=nvidia\n' > "$TARGET/.env"
if run_cli "$TARGET" preset import "$TMP_DIR/out/shared.tar.gz" > "$TMP_DIR/import.out" 2>&1 \
    && cmp -s "$SOURCE/presets/shared-setup/env" "$TARGET/presets/shared-setup/env" \
    && [[ -f "$TARGET/presets/shared-setup/meta.txt" && -f "$TARGET/presets/shared-setup/extensions.list" ]]; then
    pass "the exported archive imports with the same preset files"
else
    cat "$TMP_DIR/import.out"
    fail "the exported archive no longer imports the same preset"
fi

# Observe permissions while tar writes, and inject failures before publication.
mkdir -p "$TMP_DIR/bin"
export PRESET_REAL_TAR="$(command -v tar)"
export PRESET_REAL_CHMOD="$(command -v chmod)"
export PRESET_REAL_MV="$(command -v mv)"
export PRESET_OUTPUT="$TMP_DIR/out/transaction.tar.gz"
cat > "$TMP_DIR/bin/tar" <<'EOF'
#!/usr/bin/env bash
set -eu
if [[ "$1" == czf ]]; then
    [[ "$(cat "$PRESET_OUTPUT")" == original ]] || exit 71
    [[ "$2" != "$PRESET_OUTPUT" ]] || exit 72
    mode="$(stat -c '%a' "$2" 2>/dev/null || stat -f '%Lp' "$2")"
    [[ "$mode" == 600 ]] || exit 73
    if [[ "${PRESET_FAIL:-}" == tar ]]; then
        printf 'partial private archive' > "$2"
        exit 74
    fi
fi
exec "$PRESET_REAL_TAR" "$@"
EOF
cat > "$TMP_DIR/bin/chmod" <<'EOF'
#!/usr/bin/env bash
[[ "${PRESET_FAIL:-}" != chmod ]] || exit 75
exec "$PRESET_REAL_CHMOD" "$@"
EOF
cat > "$TMP_DIR/bin/mv" <<'EOF'
#!/usr/bin/env bash
[[ "${PRESET_FAIL:-}" != mv ]] || exit 76
exec "$PRESET_REAL_MV" "$@"
EOF
chmod +x "$TMP_DIR/bin/"*
for failure in tar chmod mv; do
    printf original > "$PRESET_OUTPUT"
    chmod 644 "$PRESET_OUTPUT"
    if PATH="$TMP_DIR/bin:$PATH" PRESET_FAIL="$failure" run_cli "$SOURCE" preset export shared-setup "$PRESET_OUTPUT" > "$TMP_DIR/failure.out" 2>&1; then
        fail "$failure failure reported success"
    elif [[ "$(cat "$PRESET_OUTPUT")" == original && "$(file_mode "$PRESET_OUTPUT")" == 644 ]] \
        && ! compgen -G "$PRESET_OUTPUT.*" >/dev/null; then
        pass "$failure failure preserves original and removes private staging"
    else
        fail "$failure failure altered original or leaked staging"
    fi
done
if PATH="$TMP_DIR/bin:$PATH" run_cli "$SOURCE" preset export shared-setup "$PRESET_OUTPUT" > "$TMP_DIR/private.out" 2>&1 \
    && [[ "$(file_mode "$PRESET_OUTPUT")" == 600 ]] \
    && tar tzf "$PRESET_OUTPUT" >/dev/null; then
    pass "archive remains private during creation and publishes atomically"
else
    cat "$TMP_DIR/private.out"
    fail "private archive creation/publication failed"
fi

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
