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
for name in _remote_provider_print_apply_result _remote_provider_print_status _remote_provider_fail \
    _remote_provider_not_serving_reason _remote_provider_transport_reason; do
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
# "ready" is set up and proven earlier, not a live answer (fleet 2026-10-18:
# it read ready through an upstream outage).
grep -qF "Ready means set up and proven; check that the API answers now with: ods remote-provider test" <<<"$output" \
    || fail "a ready route did not say how to check the API now: $output"
printf '%s' '{"status":"ready","routeState":{"enabled":true,"status":{"lastProbe":{"verifiedAt":"2026-10-18T14:05:40Z"}},"provider":{"model":"m","transport":"direct","baseUrl":"https://api.example.test/v1"}},"egress":{"status":"ok","secret":{"configured":true}},"availableActions":{"test":true}}' \
    > "$fixture/proven.json"
grep -qF "Ready means set up and proven at 2026-10-18T14:05:40Z;" <<<"$(_remote_provider_print_status "$fixture/proven.json")" \
    || fail "a ready route did not say when it was proven"

# A provider that lists its models but serves no chat is named with its own
# answer (an outage read "Remote provider probe: ok").
printf '%s' '{"ok":true,"transport":"direct","probe":{"httpStatus":200},"routeProof":{"recorded":false,"reachable":true,"reason":"provider_not_serving","statusCode":502,"providerStatus":500,"providerMessage":"Connection error."}}' \
    > "$fixture/not-serving.json"
[[ "$(_remote_provider_not_serving_reason "$fixture/not-serving.json")" == "HTTP 500: Connection error." ]] \
    || fail "the provider's answer was not named: $(_remote_provider_not_serving_reason "$fixture/not-serving.json")"
printf '%s' '{"ok":true,"routeProof":{"recorded":true}}' > "$fixture/proven-route.json"
[[ -z "$(_remote_provider_not_serving_reason "$fixture/proven-route.json")" ]] || fail "a recorded proof read as an outage"
for name in _remote_provider_probe_configured _remote_provider_print_probe_result; do
    body="$(extract "$name")"
    [[ -n "$body" ]] || fail "$name was not found in ods-cli"
    eval "$body"
done
# The Dashboard API's answer for a provider that serves no chat.
_remote_provider_request() { cp "$fixture/not-serving.json" "$3"; printf '200'; }
for mode in text json; do
    if [[ "$mode" == json ]]; then args=(--json); else args=(); fi
    if output="$( (_remote_provider_probe_configured "${args[@]}") 2>&1 )"; then
        fail "an API that serves no chat passed ods remote-provider test ($mode)"
    fi
    grep -qF "The remote provider is not answering chats (HTTP 500: Connection error.). ODS kept its settings." <<<"$output" \
        || fail "the outage was not named ($mode): $output"
    grep -qF "ods remote-provider disable" <<<"$output" || fail "no way back to the local model ($mode): $output"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "no help link ($mode): $output"
done
printf '%s' '{"ok":true,"routeProof":{"reason":"provider_not_serving","providerStatus":500,"providerMessage":"bad\\e[31m\\c"}}' \
    > "$fixture/escape.json"
[[ "$(_remote_provider_not_serving_reason "$fixture/escape.json")" == "HTTP 500: bade[31mc" ]] \
    || fail "a backslash from the provider reached echo -e: $(_remote_provider_not_serving_reason "$fixture/escape.json")"
grep -qF "did not answer in time" <<<"$(_remote_provider_transport_reason 28)" || fail "a curl timeout was not named"
grep -qF "could not connect" <<<"$(_remote_provider_transport_reason 7)" || fail "a refused connection was not named"
grep -qF "closed before" <<<"$(_remote_provider_transport_reason 52)" || fail "an empty reply was not named"

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

# Portal writes each file in one reply, so the default output limit is a
# quarter of the context window, at most 32768 (fleet 2026-10-18: the old
# 4096 default cut off a game file).
for name in _strip_outer_quotes _remote_provider_read_secret _remote_provider_write_payload; do
    body="$(extract "$name")"
    [[ -n "$body" ]] || fail "$name was not found in ods-cli"
    eval "$body"
done
error() { echo "$1" >&2; exit 1; }
payload_limits() {
    REMOTE_LLM_API_KEY=unit-test-provider-key _remote_provider_write_payload configure "$fixture/payload.json" \
        --base-url https://api.example.test/v1 --model m "$@"
    jq -r '"\(.provider.contextLength) \(.provider.maxTokens)"' "$fixture/payload.json"
}
for case in "|32768 8192" "--context-length 131072|131072 32768" "--context-length 1048576|1048576 32768" \
    "--context-length 131072 --max-tokens 4096|131072 4096"; do
    read -r -a options <<<"${case%%|*}"
    output="$(payload_limits "${options[@]}")"
    [[ "$output" == "${case#*|}" ]] || fail "options '${case%%|*}' gave context/output '$output', not '${case#*|}'"
done

echo 'PASS: remote-provider CLI output matches what happened and failures point at help'
