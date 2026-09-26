#!/usr/bin/env bash
# The install menu presets must not override an explicit --hermes/--no-hermes.
# The Windows Pixel path passes --no-hermes; picking "Full Stack" used to turn
# Hermes back on and download it next to Pixel.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
menu_source="$(sed -n '/^show_install_menu() {/,/^}/p' "$ROOT/installers/lib/ui.sh")"
[[ -n "$menu_source" ]] || { echo "FAIL: show_install_menu not found" >&2; exit 1; }
# Answer the prompt from stdin instead of the terminal.
menu_source="${menu_source//< \/dev\/tty/}"

pass=0
fail=0
# The colors and flags below are read by the eval'd show_install_menu.
# shellcheck disable=SC2034
expect() {
    local label="$1" choice="$2" explicit="$3" initial="$4" want="$5" got
    got="$(
        ai() { :; }; ai_warn() { :; }; warn() { :; }; log() { :; }; signal() { :; }
        BGRN='' AMB='' NC=''
        eval "$menu_source"
        HERMES_EXPLICIT="$explicit"
        ENABLE_HERMES="$initial"
        TIER=3
        show_install_menu >/dev/null <<<"$choice"
        printf '%s' "$ENABLE_HERMES"
    )"
    if [[ "$got" == "$want" ]]; then
        echo "PASS: $label"
        pass=$((pass + 1))
    else
        echo "FAIL: $label (ENABLE_HERMES=$got, expected $want)"
        fail=$((fail + 1))
    fi
}

expect 'Full Stack keeps explicit --no-hermes' 1 true false false
expect 'Enter (default Full Stack) keeps explicit --no-hermes' '' true false false
expect 'Invalid choice keeps explicit --no-hermes' x true false false
expect 'Core Only keeps explicit --hermes' 2 true true true
expect 'Full Stack still enables Hermes without a flag' 1 false false true
expect 'Core Only still disables Hermes without a flag' 2 false true false

echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
