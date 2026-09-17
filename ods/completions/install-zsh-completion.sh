#!/usr/bin/env bash
# Install the ODS Zsh completion into a stable per-user fpath directory.

set -euo pipefail

install_dir="${1:?usage: install-zsh-completion.sh INSTALL_DIR}"
source_file="${install_dir}/completions/_ods"
completion_dir="$HOME/.zfunc"
zshrc="${ODS_ZSHRC:-$HOME/.zshrc}"
marker="# ODS CLI zsh completion"

[[ -f "$source_file" ]] || {
    echo "ODS Zsh completion source not found: $source_file" >&2
    exit 1
}

mkdir -p "$completion_dir" "$(dirname "$zshrc")"
tmp_file="$(mktemp "${completion_dir}/.ods-completion.XXXXXX")"
trap 'rm -f "$tmp_file"' EXIT
cp "$source_file" "$tmp_file"
chmod 0644 "$tmp_file"
mv -f "$tmp_file" "$completion_dir/_ods"
trap - EXIT

if ! grep -Fq "$marker" "$zshrc" 2>/dev/null; then
    printf '\n%s\n' "$marker" >> "$zshrc"
    printf 'fpath=("$HOME/.zfunc" $fpath)\n' >> "$zshrc"
    printf 'autoload -Uz compinit && compinit\n' >> "$zshrc"
fi
