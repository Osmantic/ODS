#!/usr/bin/env bash
# Keep completions/ods-cli.bash in sync with the ods-cli dispatch table.
#
# A command that is dispatched but missing from the completion word list is
# invisible to tab-completion, and a completion entry with no dispatch case
# advertises a command that errors out. Both drift silently because nothing
# else reads the completion file.
#
# Run from ods/:  bash tests/test-cli-completion-parity.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"
COMPLETION="$ROOT_DIR/completions/ods-cli.bash"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$CLI" ]] || fail "ods-cli not found at $CLI"
[[ -f "$COMPLETION" ]] || fail "completion not found at $COMPLETION"

# Commands the CLI actually dispatches, one per line. Flag-style entries
# (--help/-h/...) are not completed as commands and are dropped.
dispatched_commands() {
    awk '/^case "\$\{1:-help\}" in$/ {inside=1; next}
         inside && /^esac$/ {exit}
         inside && match($0, /^ +[a-z0-9|_-]+\)/) {
             split(substr($0, RSTART, RLENGTH - 1), parts, "|")
             for (i in parts) {
                 gsub(/^ +| +$/, "", parts[i])
                 if (parts[i] != "" && parts[i] != "*" && parts[i] !~ /^-/) print parts[i]
             }
         }' "$CLI" | sort -u
}

# Words offered for the first argument: main_commands plus aliases.
completed_commands() {
    sed -n 's/.*local main_commands="\([^"]*\)".*/\1/p;s/.*local aliases="\([^"]*\)".*/\1/p' \
        "$COMPLETION" | tr ' ' '\n' | sed '/^$/d' | sort -u
}

dispatched="$(dispatched_commands)"
completed="$(completed_commands)"

[[ -n "$dispatched" ]] || fail "could not parse the ods-cli dispatch table"
[[ -n "$completed" ]] || fail "could not parse main_commands/aliases from the completion"

missing="$(comm -23 <(printf '%s\n' "$dispatched") <(printf '%s\n' "$completed") | tr '\n' ' ')"
missing="${missing% }"
[[ -z "$missing" ]] || fail "dispatched but not completable: $missing"
pass "every dispatched command is offered by the completion"

phantom="$(comm -13 <(printf '%s\n' "$dispatched") <(printf '%s\n' "$completed") | tr '\n' ' ')"
phantom="${phantom% }"
[[ -z "$phantom" ]] || fail "completed but not dispatched: $phantom"
pass "the completion offers no command ods-cli cannot run"

# Commands that take a subcommand need a second-level branch, otherwise the
# completion silently falls through to "no suggestions".
for cmd in gpu preset mode model remote-provider stt repair template agent config; do
    grep -qE "^[[:space:]]+([a-z0-9|_-]+\|)?${cmd}(\||\))" "$COMPLETION" \
        || fail "no second-level completion branch for '$cmd'"
done
pass "every sub-commanded command has a second-level branch"

# Service completion is a hand-maintained word list. Every service id and
# alias the CLI can resolve has to be in it, or `ods logs <TAB>` silently
# omits services the user can name.
manifest_service_names() {
    for manifest in "$ROOT_DIR"/extensions/services/*/manifest.yaml; do
        awk '/^  id:/ { print $2 }
             /^  aliases:/ {
                 line = $0
                 sub(/^[^[]*\[/, "", line)
                 sub(/\].*$/, "", line)
                 gsub(/[ \t]/, "", line)
                 n = split(line, parts, ",")
                 for (i = 1; i <= n; i++) if (parts[i] != "") print parts[i]
             }' "$manifest"
    done | sort -u
}

completed_services() {
    sed -n 's/.*local services="\([^"]*\)".*/\1/p' "$COMPLETION" \
        | tr ' ' '\n' | sed '/^$/d' | sort -u
}

manifest_names="$(manifest_service_names)"
completed_names="$(completed_services)"

[[ -n "$manifest_names" ]] || fail "could not parse service names from the manifests"
[[ -n "$completed_names" ]] || fail "could not parse the service word list from the completion"

missing_services="$(comm -23 <(printf '%s\n' "$manifest_names") <(printf '%s\n' "$completed_names") | tr '\n' ' ')"
missing_services="${missing_services% }"
[[ -z "$missing_services" ]] || fail "services the CLI resolves but the completion omits: $missing_services"
pass "every service id and alias is offered by the completion"

phantom_services="$(comm -13 <(printf '%s\n' "$manifest_names") <(printf '%s\n' "$completed_names") | tr '\n' ' ')"
phantom_services="${phantom_services% }"
[[ -z "$phantom_services" ]] || fail "completion offers unknown service names: $phantom_services"
pass "the completion offers no unknown service name"

# The mode values are a closed set in cmd_mode; keep the completion identical.
mode_cases="$(awk '/^cmd_mode\(\)/ {inside=1}
                   inside && match($0, /^ +[a-z|]+\)/) {
                       word = substr($0, RSTART, RLENGTH - 1)
                       gsub(/^ +/, "", word)
                       if (word != "*") { print word; exit }
                   }' "$CLI" | tr '|' ' ')"
mode_completion="$(sed -n '/mode|m)/,/;;/p' "$COMPLETION" \
    | sed -n 's/.*compgen -W "\([^"]*\)".*/\1/p')"
for value in $mode_cases; do
    grep -qw -- "$value" <<< "$mode_completion" \
        || fail "mode value '$value' accepted by cmd_mode but not completed"
done
pass "mode values match cmd_mode"

echo "[PASS] ods-cli completion parity"
