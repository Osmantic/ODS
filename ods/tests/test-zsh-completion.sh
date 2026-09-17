#!/usr/bin/env bash
# Public installation and command-parity contract for ODS Zsh completion.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"
COMPLETION="$ROOT_DIR/completions/_ods"
INSTALLER="$ROOT_DIR/completions/install-zsh-completion.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

[[ -f "$COMPLETION" ]] || fail "Zsh completion file is missing"
bash -n "$INSTALLER" || fail "completion installer has invalid Bash syntax"

# Extract canonical first-level commands from the actual CLI dispatch table.
while IFS= read -r command_name; do
    grep -Fq "'${command_name}:" "$COMPLETION" \
        || fail "Zsh completion is missing canonical command: $command_name"
done < <(
    sed -n '/^case "${1:-help}" in/,/^esac/p' "$CLI" \
        | sed -n 's/^[[:space:]]*\([a-z][a-z0-9-]*\)[^)]*)[[:space:]].*/\1/p'
)

# Exercise the user-visible install boundary twice: the payload is copied and
# the shell startup block remains idempotent.
fixture="$TMP_DIR/ods"
home_dir="$TMP_DIR/home"
mkdir -p "$fixture/completions" "$home_dir"
cp "$COMPLETION" "$fixture/completions/_ods"
HOME="$home_dir" bash "$INSTALLER" "$fixture"
HOME="$home_dir" bash "$INSTALLER" "$fixture"
cmp "$COMPLETION" "$home_dir/.zfunc/_ods" \
    || fail "installed completion differs from the shipped source"
[[ "$(grep -Fc '# ODS CLI zsh completion' "$home_dir/.zshrc")" == "1" ]] \
    || fail "Zsh startup hook is not idempotent"
grep -Fq 'fpath=("$HOME/.zfunc" $fpath)' "$home_dir/.zshrc" \
    || fail "Zsh startup hook does not register the completion directory"

if command -v zsh >/dev/null 2>&1; then
    zsh -n "$COMPLETION" || fail "completion has invalid Zsh syntax"
fi

echo "[PASS] Zsh completion command parity and installation"
