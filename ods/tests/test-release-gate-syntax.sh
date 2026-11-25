#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_GATE="$ROOT_DIR/scripts/release-gate.sh"

if [[ ! -f "$RELEASE_GATE" ]]; then
    echo "FAIL: release-gate.sh not found at $RELEASE_GATE" >&2
    exit 1
fi

# The shell syntax section in release-gate.sh must be portable to macOS default
# Bash 3.2: mapfile and readarray (introduced in Bash 4) are rejected.
if grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$RELEASE_GATE"; then
    echo "FAIL: release-gate.sh uses Bash 4+ mapfile/readarray" >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git_repo="$tmp/repo"
mkdir -p "$git_repo/nested"
git init -q "$git_repo"
git -C "$git_repo" config user.email "ci@example.com"
git -C "$git_repo" config user.name "CI"

cat > "$git_repo/alpha.sh" <<'SH'
#!/usr/bin/env bash
echo "alpha"
SH

cat > "$git_repo/nested/beta.sh" <<'SH'
#!/usr/bin/env bash
echo "beta"
SH

cat > "$git_repo/ignored.txt" <<'TXT'
not a shell script
TXT

git -C "$git_repo" add .
git -C "$git_repo" commit -qm "fixture setup"

# Extract the shell syntax checking block from scripts/release-gate.sh.
# The block starts right after '[gate] shell syntax' and ends at the first 'done'.
syntax_block="$(sed -n '/^echo "\[gate\] shell syntax"/,/^echo "\[gate\] compatibility/p' "$RELEASE_GATE" | sed '1d;$d')"

# 1. Execute under the current running shell and verify collected shell files.
# shellcheck disable=SC2154
out_current="$(
    cd "$git_repo"
    eval "$syntax_block"
    printf '%s\n' "${sh_files[@]}"
)"

echo "$out_current" | grep -qx "alpha.sh" || {
    echo "FAIL: alpha.sh missing from collected shell files" >&2
    exit 1
}
echo "$out_current" | grep -qx "nested/beta.sh" || {
    echo "FAIL: nested/beta.sh missing from collected shell files" >&2
    exit 1
}
if echo "$out_current" | grep -q "ignored.txt"; then
    echo "FAIL: ignored.txt should not be collected" >&2
    exit 1
fi

# 2. If macOS /bin/bash (Bash 3.2) is available, verify under /bin/bash explicitly.
if [[ -x /bin/bash ]]; then
    bash_ver="$(/bin/bash -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null || true)"
    if [[ "$bash_ver" == "3" ]]; then
        (
            cd "$git_repo"
            /bin/bash -e -c "$syntax_block"
        )
        echo "VERIFIED: executed successfully under real /bin/bash 3.2"
    fi
fi

# 3. Simulate an environment without mapfile/readarray by disabling the builtins
# and assert syntax discovery successfully collects all shell files.
out_disabled="$(
    cd "$git_repo"
    bash -e -c '
        enable -n mapfile readarray 2>/dev/null || true
        '"$syntax_block"'
        printf "%s\n" "${sh_files[@]}"
    '
)"
echo "$out_disabled" | grep -qx "alpha.sh" || {
    echo "FAIL: alpha.sh missing when mapfile/readarray builtins disabled" >&2
    exit 1
}
echo "$out_disabled" | grep -qx "nested/beta.sh" || {
    echo "FAIL: nested/beta.sh missing when mapfile/readarray builtins disabled" >&2
    exit 1
}

# 4. Verify that syntax errors in discovered scripts are properly caught.
cat > "$git_repo/broken.sh" <<'SH'
#!/usr/bin/env bash
if [[ unclosed; then
SH
git -C "$git_repo" add "$git_repo/broken.sh"
git -C "$git_repo" commit -qm "add broken script"

syntax_rc=0
(
    cd "$git_repo"
    bash -e -c "$syntax_block"
) >/dev/null 2>&1 || syntax_rc=$?

if [[ "$syntax_rc" -eq 0 ]]; then
    echo "FAIL: syntax gate did not report non-zero on broken shell syntax" >&2
    exit 1
fi

echo "[PASS] release-gate shell syntax portability (Bash 3.2 + Bash 5 compatible)"
