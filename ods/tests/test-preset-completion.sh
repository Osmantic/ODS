#!/usr/bin/env bash
# Exercise registered Bash completion against the real preset save producer.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
export HOME="$tmp/home" INSTALL_DIR="$tmp/install with spaces" ODS_HOME="$tmp/foreign"
mkdir -p "$HOME" "$INSTALL_DIR" "$ODS_HOME/presets/foreign-only"
printf 'sentinel\n' > "$ODS_HOME/sentinel"
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
check_install() { :; }
sr_load() { SERVICE_IDS=(); }
log() { :; }
success() { :; }
error() { fail "$*"; }
# Extract only the production preset entry point; do not source CLI dispatch or
# invoke any lifecycle commands. The save arm writes solely in our fixture.
eval "$(sed -n '/^cmd_preset() {/,/^}/p' "$ROOT/ods-cli")"
PRESETS_DIR="$INSTALL_DIR/presets"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
cmd_preset save 'alpha'
cmd_preset save 'alpha with spaces'
cmd_preset save 'literal.preset'
cmd_preset save '.hidden'
printf 'not a preset directory\n' > "$PRESETS_DIR/unrelated.preset"
mkdir -p "$INSTALL_DIR/.presets/obsolete-only"
_init_completion() {
    words=("${COMP_WORDS[@]}")
    cword="$COMP_CWORD"
    cur="${words[cword]}"
    prev="${words[cword-1]}"
}
source "$ROOT/completions/ods-cli.bash"
complete_names() {
    COMP_WORDS=("$@")
    COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
    COMPREPLY=()
    _ods_completion
}
expect_names() {
    local expected="$1" actual
    actual="$(printf '%s\n' "${COMPREPLY[@]}")"
    [[ "$actual" == "$expected" ]] || fail "wanted [$expected], got [$actual]"
}
for command in preset p; do
    for action in save s load l delete rm export e diff d; do
        complete_names ods "$command" "$action" alpha
        expect_names $'alpha\nalpha with spaces'
    done
done
complete_names ods preset load literal
expect_names 'literal.preset'
complete_names ods preset load .
expect_names '.hidden'
shopt -s dotglob
complete_names ods preset load .
expect_names '.hidden'
shopt -q dotglob || fail 'completion changed caller dotglob option'
shopt -u dotglob
complete_names ods preset load unrelated
[[ ${#COMPREPLY[@]} == 0 ]] || fail 'included a regular file as a preset'
complete_names ods preset load obsolete
[[ ${#COMPREPLY[@]} == 0 ]] || fail 'included retired .presets storage'
complete_names ods preset diff alpha 'alpha w'
expect_names 'alpha with spaces'
complete_names ods p d alpha 'literal'
expect_names 'literal.preset'
complete_names ods preset load missing
[[ ${#COMPREPLY[@]} == 0 ]] || fail 'unmatched prefix left stale completion'

# Explicit selectors agree with the CLI, and sourcing a custom installed copy
# finds its own install without requiring exported variables or a fixed cwd.
unset INSTALL_DIR
complete_names ods preset load foreign
expect_names 'foreign-only'
unset ODS_HOME
export ODS_INSTALL_DIR="$tmp/other-install"
mkdir -p "$ODS_INSTALL_DIR/presets/other-only"
complete_names ods preset load other
expect_names 'other-only'
unset ODS_INSTALL_DIR
custom="$tmp/custom install"
mkdir -p "$custom/completions" "$custom/presets/custom-only"
cp "$ROOT/completions/ods-cli.bash" "$custom/completions/ods-cli.bash"
: > "$custom/docker-compose.base.yml"
source "$custom/completions/ods-cli.bash"
cd "$tmp"
complete_names ods preset load custom
expect_names 'custom-only'
# A system-installed completion file has no adjacent compose marker.
mkdir -p "$tmp/system-completions" "$HOME/ods/presets/home-only"
cp "$ROOT/completions/ods-cli.bash" "$tmp/system-completions/ods-cli.bash"
source "$tmp/system-completions/ods-cli.bash"
complete_names ods preset load home
expect_names 'home-only'
[[ "$(cat "$tmp/foreign/sentinel")" == sentinel ]] || fail 'foreign state changed'
printf 'PASS: preset completion follows directory storage, preserves names, and respects install selectors\n'
