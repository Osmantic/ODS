#!/usr/bin/env bash
# `ods model recover` is the CLI form of the dashboard's "Recover model
# switch". When the previous model cannot be proven but the switch changed
# nothing, it names the release command; --release-unverified sends exactly
# that request (fleet row 27: a laptop had no supported way out).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || fail "jq is required"

body="$(awk '/^cmd_model\(\) \{/ { emit = 1 }
    emit { print }
    emit && /^}$/ { exit }' "$ROOT/ods-cli")"
[[ -n "$body" ]] || fail "cmd_model was not found in ods-cli"
eval "$body"
eval "$(grep -E '^(success|error)[(][)] ' "$ROOT/ods-cli")"
RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
INSTALL_DIR="$fixture"
check_install() { :; }
_remote_provider_request() {
    printf '%s %s\n' "$1" "$2" > "$fixture/request"
    cat "$4" > "$fixture/payload"
    printf '%s' "$STUB_BODY" > "$3"
    printf '%s' "$STUB_CODE"
}

recover() { ( cmd_model recover "$@" ) 2>&1; }

STUB_CODE=200 STUB_BODY='{"pending":false,"phase":"completed","transactionId":"'"$(printf 'a%.0s' {1..64})"'","outcome":"rollback"}'
export STUB_CODE STUB_BODY
output="$(recover)" || fail "a finished recovery exited non-zero: $output"
grep -qF "The interrupted model switch was finished" <<<"$output" || fail "no finished message: $output"
[[ "$(cat "$fixture/request")" == "POST /api/models/recovery" ]] || fail "wrong request: $(cat "$fixture/request")"
[[ "$(cat "$fixture/payload")" == "{}" ]] || fail "a plain recover sent $(cat "$fixture/payload")"

export STUB_BODY='{"pending":false,"phase":"idle","transactionId":null}'
output="$(recover)" || fail "an idle recovery exited non-zero: $output"
grep -qF "No model switch is waiting for recovery." <<<"$output" || fail "no idle message: $output"

export STUB_CODE=409 STUB_BODY='{"pending":true,"phase":"held","transactionId":"x","reason":"model-recovery-proof-required","releasable":true}'
if output="$(recover)"; then fail "an unproven recovery exited 0"; fi
grep -qF "could not confirm the model Portal used before this switch" <<<"$output" || fail "no cause: $output"
grep -qF "ods model recover --release-unverified" <<<"$output" || fail "the release command was not named: $output"
grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "no help line: $output"

if output="$(recover --release-unverified)"; then fail "a refused release exited 0"; fi
[[ "$(cat "$fixture/payload")" == '{"releaseUnverified":true}' ]] || fail "the release sent $(cat "$fixture/payload")"

export STUB_BODY='{"pending":true,"phase":"applied","transactionId":"x","reason":"model-recovery-proof-required","releasable":false}'
if output="$(recover)"; then fail "a non-releasable recovery exited 0"; fi
grep -qF "still needs repair" <<<"$output" || fail "no repair message: $output"
grep -qF -- "--release-unverified" <<<"$output" && fail "a switch that changed something was offered a release: $output"

if output="$(recover --force)"; then fail "an unknown option exited 0"; fi
grep -qF "Usage: ods model recover [--release-unverified]" <<<"$output" || fail "no usage: $output"

echo "PASS: ods model recover reports each outcome and offers a release only where one applies"
