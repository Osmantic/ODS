#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/home/ods"
touch "$tmp/home/ods/.env" "$tmp/home/ods/ods-cli" \
    "$tmp/home/ods/ods-uninstall.sh" "$tmp/home/ods/docker-compose.base.yml"
{
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'warn() { echo "$*"; }'
    sed -n '/^validate_force_reinstall_target() {/,/^}/p' "$ROOT/get-ods.sh"
    sed -n '/^is_truthy() {/,/^# ── Banner/p' "$ROOT/get-ods.sh" | sed '$d'
    printf '%s\n' 'docker() { cat "$ROWS"; }' 'refuse_legacy_install'
} > "$tmp/guard.sh"
export ODS_BOOTSTRAP_ROOT="$tmp/home" INSTALL_DIR="$tmp/home/ods"
export PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" ROWS="$tmp/rows"
write_stack() {
    local project="$1" directory="$2"
    printf '%s\n' "$project-llm|$project|llama-server|$directory" \
        "$project-web|$project|open-webui|$directory" \
        "$project-api|$project|dashboard-api|$directory"
}
check() {
    local expected="$1" label="$2" rc=0
    bash "$tmp/guard.sh" >"$tmp/result" 2>&1 || rc=$?
    if [[ "$expected" == pass && "$rc" != 0 ]] || [[ "$expected" == block && "$rc" == 0 ]]; then
        cat "$tmp/result"
        echo "FAIL: $label"
        exit 1
    fi
    echo "PASS: $label"
}
write_stack ods "$INSTALL_DIR" > "$ROWS"
export BOOTSTRAP_REINSTALL=true
check pass 'validated forced reinstall permits its own complete Compose stack'
export BOOTSTRAP_REINSTALL=false
check block 'first install still rejects an existing complete stack'
export BOOTSTRAP_REINSTALL=true
write_stack ods '' > "$ROWS"
check block 'missing working-directory labels fail closed'
write_stack ods "$tmp/other" > "$ROWS"
check block 'foreign working-directory labels fail closed'
write_stack ods "$INSTALL_DIR" > "$ROWS"
printf 'foreign-extra|ods|searxng|%s\n' "$tmp/other" >> "$ROWS"
check block 'one foreign row blocks even when it reuses the target project name'
write_stack ods "$INSTALL_DIR" > "$ROWS"
write_stack other "$tmp/other" >> "$ROWS"
check block 'independent foreign stack still blocks forced reinstall'
write_stack ods "$INSTALL_DIR" > "$ROWS"
rm "$INSTALL_DIR/ods-cli"
check block 'unfingerprinted root cannot claim its Compose project'
touch "$INSTALL_DIR/ods-cli"
mv "$INSTALL_DIR" "$tmp/home/real-ods"
ln -s "$tmp/home/real-ods" "$INSTALL_DIR"
check block 'symlinked installation cannot claim its Compose project'
echo 'Bootstrap forced-reinstall Compose ownership checks passed.'
