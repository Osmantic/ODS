#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/installers/macos/lib/env-generator.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
env_path="$scratch/.env"

umask 022
printf 'SECRET=keep-private\nENABLE_OPEN_WEBUI=true\n' > "$env_path"
chmod 600 "$env_path"
before_inode="$(ls -i "$env_path" | awk '{print $1}')"
ln "$env_path" "$scratch/bind-view"

upsert_env_value "$env_path" ENABLE_OPEN_WEBUI false
grep -qx 'ENABLE_OPEN_WEBUI=false' "$env_path"
grep -qx 'SECRET=keep-private' "$env_path"
grep -qx 'ENABLE_OPEN_WEBUI=false' "$scratch/bind-view"
[[ "$(ls -i "$env_path" | awk '{print $1}')" == "$before_inode" ]]
[[ "$(stat -c %a "$env_path" 2>/dev/null || stat -f %Lp "$env_path")" == 600 ]]

# Simulate failure after the live file has been opened and truncated. The
# rollback must restore the exact bytes while retaining its inode and mode.
cp "$env_path" "$scratch/committed"
cat() {
    local stage_dir
    stage_dir="$(dirname "$1")"
    printf '%s %s %s\n' \
        "$(stat -c %a "$stage_dir" 2>/dev/null || stat -f %Lp "$stage_dir")" \
        "$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1")" \
        "$(stat -c %a "$stage_dir/previous" 2>/dev/null || stat -f %Lp "$stage_dir/previous")" \
        > "$scratch/staged-modes"
    printf 'partial'
    return 1
}
if upsert_env_value "$env_path" ENABLE_OPEN_WEBUI true; then
    echo 'failed live write incorrectly succeeded' >&2
    exit 1
fi
unset -f cat
cmp -s "$env_path" "$scratch/committed"
cmp -s "$scratch/bind-view" "$scratch/committed"
[[ "$(< "$scratch/staged-modes")" == '700 600 600' ]]
[[ "$(ls -i "$env_path" | awk '{print $1}')" == "$before_inode" ]]
[[ "$(stat -c %a "$env_path" 2>/dev/null || stat -f %Lp "$env_path")" == 600 ]]
[[ -z "$(find "$scratch" -maxdepth 1 -name '.env.stage.*' -print)" ]]

# No terminal newline and a previously absent file must also be safe.
printf 'SECRET=keep-private' > "$env_path"
upsert_env_value "$env_path" NEW_KEY 'literal\backslash'
grep -qx 'SECRET=keep-private' "$env_path"
grep -Fxq 'NEW_KEY=literal\backslash' "$env_path"
upsert_env_value "$scratch/new.env" NEW_KEY value
[[ "$(stat -c %a "$scratch/new.env" 2>/dev/null || stat -f %Lp "$scratch/new.env")" == 600 ]]

ln -s "$env_path" "$scratch/link.env"
if upsert_env_value "$scratch/link.env" NEW_KEY value; then
    echo 'symlinked .env was accepted' >&2
    exit 1
fi

echo 'PASS: Mac .env upsert keeps private bytes, inode, and rollback'
