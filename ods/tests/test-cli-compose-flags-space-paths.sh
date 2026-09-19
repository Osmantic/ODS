#!/usr/bin/env bash
# Regression (issue #3340): get_compose_flags validates the .compose-flags cache
# by iterating `for tok in $cached`, which word-splits every compose path on
# whitespace. An install under a directory containing spaces (or any compose
# path with spaces) produces a cache like `-f sub dir/compose.yaml`, and the
# validator checks the fragment `sub` instead of `sub dir/compose.yaml` —
# wrongly discarding a valid cache, or wrongly keeping a stale one.
#
# The cache is a sequence of `-f <path>` pairs; the fix walks those pairs so
# spaced paths stay intact.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Extract the real function under test — no reimplementation.
FUNC_SRC="$(awk '/^get_compose_flags\(\) \{/,/^\}/' "$CLI")"
[[ -n "$FUNC_SRC" ]] || fail "could not extract get_compose_flags from ods-cli"

run_get_compose_flags() {
    # Args: <install_dir>. Prints get_compose_flags' stdout.
    local install_dir="$1"
    bash -c '
        set -euo pipefail
        INSTALL_DIR="$1"
        eval "$2"
        _ensure_hermes_dashboard_session_token() { :; }
        sr_compose_flags() { printf ""; }
        get_compose_flags
    ' _ "$install_dir" "$FUNC_SRC"
}

# ── Case 1: spaced relative + absolute paths, all present → cache honoured ──
install_dir="$tmp/install dir"
mkdir -p "$install_dir/sub dir"
touch "$install_dir/sub dir/compose.yaml" "$install_dir/docker-compose.base.yml"

cat > "$install_dir/.compose-flags" <<'EOF'
-f sub dir/compose.yaml -f docker-compose.base.yml
EOF

out="$(run_get_compose_flags "$install_dir")"
[[ "$out" == "-f sub dir/compose.yaml -f docker-compose.base.yml" ]] \
    || fail "valid cache with spaced path was not honoured; got: $out"
[[ -f "$install_dir/.compose-flags" ]] || fail "valid cache file was removed"
pass "spaced relative path in cache is validated whole and honoured"

# ── Case 2: spaced path missing → cache declared stale, resolver used ───────
rm "$install_dir/sub dir/compose.yaml"
mkdir -p "$install_dir/scripts"
cat > "$install_dir/scripts/resolve-compose-stack.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "-f resolver-output.yml"
EOF
chmod +x "$install_dir/scripts/resolve-compose-stack.sh"

out="$(run_get_compose_flags "$install_dir")"
[[ "$out" == "-f resolver-output.yml" ]] \
    || fail "stale spaced-path cache did not fall back to resolver; got: $out"
[[ ! -f "$install_dir/.compose-flags" ]] || fail "stale cache file was not removed"
pass "missing spaced-path file marks cache stale and falls back to resolver"

# ── Case 3: spaced absolute path honoured ───────────────────────────────────
abs_dir="$tmp/abs dir"
mkdir -p "$abs_dir"
touch "$abs_dir/extra.yml"
cat > "$install_dir/.compose-flags" <<EOF
-f docker-compose.base.yml -f $abs_dir/extra.yml
EOF

out="$(run_get_compose_flags "$install_dir")"
[[ "$out" == "-f docker-compose.base.yml -f $abs_dir/extra.yml" ]] \
    || fail "valid cache with spaced absolute path was not honoured; got: $out"
pass "spaced absolute path in cache is validated whole and honoured"

echo "All compose-flags space-path checks passed."
