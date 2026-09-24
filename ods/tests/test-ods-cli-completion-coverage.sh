#!/usr/bin/env bash
# ============================================================================
# ods-cli ↔ bash-completion coverage contract
# ============================================================================
# The completion's main_commands/aliases lists drifted from the dispatcher:
# purge, remote-provider, stt, rollback, repair, audit, template, and agent
# were real commands that no amount of <TAB> would ever suggest, and the log,
# fix, and tmpl aliases were missing too.
#
# Parse the dispatcher's `case "${1:-help}" in` arm labels out of ods-cli:
# the first token of each `a|b|c)` arm is a primary command (must appear in
# main_commands); the rest are aliases (must appear in aliases). Checked in
# both directions so stale entries get caught too.
#
# Usage: ./tests/test-ods-cli-completion-coverage.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"
COMPLETIONS="$ROOT_DIR/completions/ods-cli.bash"

PASSED=0
FAILED=0
pass() { echo "  ✓ PASS $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  ✗ FAIL $1"; FAILED=$((FAILED + 1)); }

[[ -f "$ODS_CLI" && -f "$COMPLETIONS" ]] || { echo "missing ods-cli or completions file"; exit 1; }

# Arm labels from the trailing dispatcher case: lines like `    gpu|g)` or
# `    status-json)`. First label token -> command; the rest -> aliases.
# The `*)` catch-all and flag forms (-h, --help, -v, --version) are not
# word-completions and are excluded.
commands=()
alias_names=()
while IFS= read -r arm; do
    IFS='|' read -ra parts <<< "$arm"
    [[ -n "${parts[0]:-}" ]] && commands+=("${parts[0]}")
    for ((i = 1; i < ${#parts[@]}; i++)); do
        [[ "${parts[$i]}" == -* ]] || alias_names+=("${parts[$i]}")
    done
done < <(
    sed -n '/^case "${1:-help}" in/,/^esac$/p' "$ODS_CLI" \
        | grep -oE '^[[:space:]]+[a-z][a-z0-9-]*(\|[a-z0-9-]+)*\)' \
        | tr -d ' )'
)
[[ ${#commands[@]} -gt 20 ]] || { echo "dispatcher parse produced ${#commands[@]} commands — extraction broke"; exit 1; }

# Word lists from the completion file.
main_commands=$(grep -oE 'local main_commands="[^"]+"' "$COMPLETIONS" | cut -d'"' -f2)
aliases=$(grep -oE 'local aliases="[^"]+"' "$COMPLETIONS" | cut -d'"' -f2)
[[ -n "$main_commands" && -n "$aliases" ]] || { echo "could not read completion word lists"; exit 1; }

missing_cmds=""
for token in "${commands[@]}"; do
    [[ " $main_commands " == *" $token "* ]] || missing_cmds+=" $token"
done
if [[ -z "$missing_cmds" ]]; then
    pass "every dispatcher command name appears in main_commands"
else
    fail "commands missing from main_commands:$missing_cmds"
fi

missing_aliases=""
for token in "${alias_names[@]}"; do
    [[ " $aliases " == *" $token "* ]] || missing_aliases+=" $token"
done
if [[ -z "$missing_aliases" ]]; then
    pass "every dispatcher alias appears in aliases"
else
    fail "aliases missing from aliases:$missing_aliases"
fi

stale_cmds=""
for word in $main_commands; do
    found=false
    for token in "${commands[@]}"; do
        [[ "$word" == "$token" ]] && { found=true; break; }
    done
    $found || stale_cmds+=" $word"
done
if [[ -z "$stale_cmds" ]]; then
    pass "every main_commands entry is a real command (no stale entries)"
else
    fail "main_commands lists non-commands:$stale_cmds"
fi

stale_aliases=""
for word in $aliases; do
    found=false
    for token in "${alias_names[@]}"; do
        [[ "$word" == "$token" ]] && { found=true; break; }
    done
    $found || stale_aliases+=" $word"
done
if [[ -z "$stale_aliases" ]]; then
    pass "every aliases entry is a real alias (no stale entries)"
else
    fail "aliases lists non-aliases:$stale_aliases"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
