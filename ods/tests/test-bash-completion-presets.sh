#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d -t ods-preset-completion-XXXXXX)"
trap 'rm -rf "$TEST_ROOT"' EXIT

mkdir -p "$TEST_ROOT/presets/mysetup" "$TEST_ROOT/presets/another"
mkdir -p "$TEST_ROOT/.presets/legacy-only"
export ODS_HOME="$TEST_ROOT"

_init_completion() {
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD - 1]}"
    words=("${COMP_WORDS[@]}")
    cword="$COMP_CWORD"
}

# shellcheck source=completions/ods-cli.bash
source "$ROOT_DIR/completions/ods-cli.bash"

assert_preset_completion() {
    local action="$1"
    COMP_WORDS=(ods preset "$action" my)
    COMP_CWORD=3
    COMPREPLY=()
    _ods_completion

    [[ " ${COMPREPLY[*]} " == *" mysetup "* ]]
    [[ " ${COMPREPLY[*]} " != *" legacy-only "* ]]
}

assert_preset_completion load
assert_preset_completion delete
assert_preset_completion export

echo "bash preset completion tests passed"
