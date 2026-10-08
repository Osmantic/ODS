#!/usr/bin/env bash
# `ods remote-provider` output matches what happened: a probe shows the HTTP
# status it got (it read a field the receipt never has, so it printed
# "HTTP unknown"), "Saved route" appears only while the route is off (it read
# "none" right after a working enable), and failures end with the help line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || fail "jq is required"

extract() {
    awk -v signature="^$1[(][)]" '
        $0 ~ signature { in_block = 1 }
        in_block { print }
        in_block && /^}$/ { exit }
    ' "$ROOT/ods-cli"
}
for name in _remote_provider_print_apply_result _remote_provider_print_status _remote_provider_fail; do
    body="$(extract "$name")"
    [[ -n "$body" ]] || fail "$name was not found in ods-cli"
    eval "$body"
done
RED="" NC=""

printf '%s' '{"action":"configure","applied":true,"mutated":true,"probe":{"httpStatus":200,"endpoint":"/v1/models","modelCount":1}}' \
    > "$fixture/apply.json"
output="$(_remote_provider_print_apply_result "$fixture/apply.json")"
grep -qF "Probe: HTTP 200 at /v1/models (1 models)" <<<"$output" || fail "the probe status was not shown: $output"

printf '%s' '{"status":"ready","routeState":{"enabled":true,"resumeAvailable":false,"provider":{"model":"m","transport":"direct","baseUrl":"https://api.example.test/v1"}},"egress":{"status":"ok","secret":{"configured":true}},"availableActions":{"test":true}}' \
    > "$fixture/on.json"
output="$(_remote_provider_print_status "$fixture/on.json")"
grep -q "Saved route" <<<"$output" && fail "an active route was reported as having no saved route: $output"
grep -qF "Provider: m via direct at https://api.example.test/v1" <<<"$output" || fail "the active provider was not shown: $output"

printf '%s' '{"status":"paused","routeState":{"enabled":true,"provider":{"model":"m","transport":"direct","baseUrl":"https://api.example.test/v1"}},"egress":{"status":"ok","secret":{"configured":true}},"availableActions":{"enable":true}}' \
    > "$fixture/paused.json"
output="$(_remote_provider_print_status "$fixture/paused.json")"
grep -qF "paused it; ODS uses the model on this computer. Use it again with: ods remote-provider enable" <<<"$output" \
    || fail "a paused route did not say how to use it again: $output"

printf '%s' '{"status":"disabled","routeState":{"enabled":false,"resumeAvailable":true},"egress":{"status":"ok","secret":{"configured":true}},"availableActions":{"enable":true}}' \
    > "$fixture/off.json"
output="$(_remote_provider_print_status "$fixture/off.json")"
grep -qF "Saved route: available (resume it with: ods remote-provider enable)" <<<"$output" \
    || fail "a paused route did not say how to resume it: $output"

if output="$( (_remote_provider_fail "Remote-provider test failed (HTTP 500): boom") 2>&1 )"; then
    fail "a failure exited 0"
fi
grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "a failure did not point at help: $output"

echo 'PASS: remote-provider CLI output matches what happened and failures point at help'
