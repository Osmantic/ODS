#!/usr/bin/env bash
# Test installers/lib/path-utils.sh: normalize_path must normalize on every
# platform, not only where GNU realpath happens to be the `realpath` on PATH.
#
# macOS 12.3+ ships a BSD realpath. It has no -m and refuses paths whose
# components do not exist yet, and it occupies the plain `realpath` name — so
# a name-only probe picks it, its call fails, and the install dir is handed
# back unnormalized. installers/macos/lib/constants.sh derives
# ODS_INSTALL_DIR from this function.
#
# Run from repo root:  bash ods/tests/test-path-normalization.sh
# Or from ods:         bash tests/test-path-normalization.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$ROOT_DIR/installers/lib/path-utils.sh" ]] || fail "installers/lib/path-utils.sh not found"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# A stand-in for BSD realpath: rejects -m and refuses missing components.
mkdir -p "$tmpdir/bsd"
# An absolute shebang: PATH is emptied out below, so `env` could not find a
# shell to hand off to.
cat > "$tmpdir/bsd/realpath" << 'EOF'
#!/bin/sh
if [ "$1" = "-m" ]; then
    echo "realpath: illegal option -- m" >&2
    exit 1
fi
[ -e "$1" ] || { echo "realpath: $1: No such file or directory" >&2; exit 1; }
EOF
chmod +x "$tmpdir/bsd/realpath"

# A stand-in for a platform with no realpath of any kind.
mkdir -p "$tmpdir/bare"

normalize_with() {
    local stub_dir="$1" input="$2"
    # PATH is replaced outright so the host's real realpath cannot leak in;
    # coreutils used by the function are bash builtins.
    PATH="$stub_dir" "$BASH" -c '
        . "'"$ROOT_DIR"'/installers/lib/path-utils.sh"
        normalize_path "$1"
    ' _ "$input"
}

for platform in bsd bare; do
    echo "Test: normalize_path on a host with the '$platform' realpath"

    got="$(normalize_with "$tmpdir/$platform" "/Users/example/ods/")"
    [[ "$got" == "/Users/example/ods" ]] || fail "$platform: trailing slash not stripped (got '$got')"

    got="$(normalize_with "$tmpdir/$platform" "/Users/example//ods")"
    [[ "$got" == "/Users/example/ods" ]] || fail "$platform: duplicate slash not collapsed (got '$got')"

    got="$(normalize_with "$tmpdir/$platform" "/Users/example/./ods")"
    [[ "$got" == "/Users/example/ods" ]] || fail "$platform: '.' segment kept (got '$got')"

    # The install dir does not exist yet during a first install, which is what
    # -m exists for and what BSD realpath refuses outright.
    got="$(normalize_with "$tmpdir/$platform" "/Users/example/tmp/../ods")"
    [[ "$got" == "/Users/example/ods" ]] || fail "$platform: '..' segment kept (got '$got')"

    got="$(normalize_with "$tmpdir/$platform" "/")"
    [[ "$got" == "/" ]] || fail "$platform: root did not survive (got '$got')"

    got="$(normalize_with "$tmpdir/$platform" "/..")"
    [[ "$got" == "/" ]] || fail "$platform: /.. is not / (got '$got')"

    pass "$platform host normalizes the install dir"
done

echo "Test: GNU realpath is still preferred when it is available"
mkdir -p "$tmpdir/gnu"
cat > "$tmpdir/gnu/grealpath" << 'EOF'
#!/bin/sh
echo "GREALPATH-WAS-USED"
EOF
chmod +x "$tmpdir/gnu/grealpath"
cp "$tmpdir/bsd/realpath" "$tmpdir/gnu/realpath"
got="$(normalize_with "$tmpdir/gnu" "/Users/example/ods/")"
[[ "$got" == "GREALPATH-WAS-USED" ]] || fail "grealpath was shadowed by the BSD realpath (got '$got')"
pass "grealpath wins over a realpath that cannot do the job"

echo "Test: empty input still reports failure"
if normalize_with "$tmpdir/bare" "" >/dev/null 2>&1; then
    fail "empty path should return non-zero"
fi
pass "empty path returns non-zero"

echo ""
echo "All path normalization tests passed."
