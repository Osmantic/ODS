#!/usr/bin/env bash
# Behavioral checks for completions/ods-cli.bash.
#
# The completion resolves preset names from the install dir. Presets are
# stored as directories under <install>/presets/ (see PRESETS_DIR in
# ods-cli); completing from a .presets path or stripping a .preset suffix
# never matches real installs.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPLETIONS="$ROOT_DIR/completions/ods-cli.bash"
TMP_DIR=""

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

# _init_completion populates the caller's locals through dynamic scope the
# same way the real bash-completion helper does.
_init_completion() {
    words=("${TEST_WORDS[@]}")
    cword=$(( ${#words[@]} - 1 ))
    cur="${words[cword]}"
    prev="${words[cword-1]}"
    return 0
}

main() {
    [[ -f "$COMPLETIONS" ]] || fail "missing $COMPLETIONS"

    TMP_DIR="$(mktemp -d -t ods-completion-test-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    # Presets are directories under <install>/presets/, named verbatim.
    mkdir -p "$TMP_DIR/ods/presets/keepme" "$TMP_DIR/ods/presets/workstation"
    # Backup IDs live in <install>/.backups/<timestamped id>.
    mkdir -p "$TMP_DIR/ods/.backups/20260918-120000" "$TMP_DIR/ods/.backups/20260917-090000"

    export ODS_HOME="$TMP_DIR/ods"
    # shellcheck disable=SC1090
    . "$COMPLETIONS"

    local TEST_WORDS got

    TEST_WORDS=(ods preset load ke)
    COMPREPLY=()
    _ods_completion
    got="${COMPREPLY[*]:-}"
    [[ "$got" == *"keepme"* ]] \
        || fail "preset load must complete preset dir names (got: '$got')"
    pass "preset load completes preset names"

    TEST_WORDS=(ods preset delete work)
    COMPREPLY=()
    _ods_completion
    got="${COMPREPLY[*]:-}"
    [[ "$got" == *"workstation"* ]] \
        || fail "preset delete must complete preset dir names (got: '$got')"
    pass "preset delete completes preset names"

    TEST_WORDS=(ods preset export keep)
    COMPREPLY=()
    _ods_completion
    got="${COMPREPLY[*]:-}"
    [[ "$got" == *"keepme"* ]] \
        || fail "preset export must complete preset dir names (got: '$got')"
    pass "preset export completes preset names"

    TEST_WORDS=(ods restore 2026)
    COMPREPLY=()
    _ods_completion
    got="${COMPREPLY[*]:-}"
    [[ "$got" == *"20260918-120000"* ]] \
        || fail "restore must complete backup IDs (got: '$got')"
    pass "restore completes backup IDs"
}

main "$@"
