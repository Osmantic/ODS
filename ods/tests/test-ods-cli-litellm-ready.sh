#!/usr/bin/env bash
# A whole-stack start or restart waits for LiteLLM's readiness endpoint before
# it returns, so the first Portal chat after it does not fail (fleet F58). A
# slow gateway warns without failing the start; a stack without LiteLLM skips.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

extract_function() {
    awk -v signature="$1()" '
        $0 == signature " {" { in_fn=1 }
        in_fn { print }
        in_fn && $0 == "}" { exit }
    ' "$ROOT/ods-cli"
}

wait_body="$(extract_function _ods_cli_wait_for_litellm_ready)"
[[ -n "$wait_body" ]] || { echo "FAIL: _ods_cli_wait_for_litellm_ready was not found in ods-cli"; exit 1; }
eval "$wait_body"

messages=""
log() { messages+="log:$1"$'\n'; }
success() { messages+="ok:$1"$'\n'; }
warn() { messages+="warn:$1"$'\n'; }
sleep() { :; }

probes=0
docker() {
    if [[ "$1" == inspect && "$2" == ods-litellm ]]; then
        return "${MOCK_NO_LITELLM:-0}"
    fi
    if [[ "$1" == exec && "$2" == ods-litellm && "$*" == *"/health/readiness"* ]]; then
        probes=$((probes + 1))
        (( probes >= ${MOCK_READY_AT:-999999} ))
        return
    fi
    echo "unexpected docker call: $*" >&2
    return 99
}

fail() { echo "FAIL: $1"; printf '%s' "$messages"; exit 1; }

# Ready on the third probe: returns once it answers.
messages="" probes=0
MOCK_READY_AT=3 _ods_cli_wait_for_litellm_ready
[[ "$probes" -eq 3 ]] || fail "expected 3 readiness probes, got $probes"
[[ "$messages" == *"ok:Model gateway — ready"* ]] || fail "ready gateway not reported"
[[ "$messages" != *"warn:"* ]] || fail "ready gateway warned"

# Never ready within the bound: probes timeout/interval times, warns, succeeds.
messages="" probes=0
rc=0
ODS_LITELLM_READY_TIMEOUT=6 ODS_LITELLM_READY_INTERVAL=2 _ods_cli_wait_for_litellm_ready || rc=$?
[[ "$rc" -eq 0 ]] || fail "a slow gateway failed the start (rc $rc)"
[[ "$probes" -eq 3 ]] || fail "expected 3 probes in 6 s at 2 s intervals, got $probes"
[[ "$messages" == *"warn:The model gateway (LiteLLM) is still starting after 6s"* ]] || fail "slow gateway not explained"
[[ "$messages" == *"ods status"* ]] || fail "slow gateway warning names no next step"

# No LiteLLM container: no wait at all.
messages="" probes=0
MOCK_NO_LITELLM=1 _ods_cli_wait_for_litellm_ready
[[ "$probes" -eq 0 ]] || fail "probed a stack without LiteLLM"
[[ -z "$messages" ]] || fail "a stack without LiteLLM printed: $messages"

# Both lifecycle commands wait after Compose for a whole stack or LiteLLM.
for command in cmd_start cmd_restart; do
    body="$(extract_function "$command")"
    grep -qF '"$resolved_service" == "litellm" ]]; then' <<< "$body" \
        || fail "$command does not gate the wait on a whole stack or LiteLLM"
    grep -qx '        _ods_cli_wait_for_litellm_ready' <<< "$body" \
        || fail "$command does not wait for LiteLLM"
    # The Compose invocation itself, not comments that mention it.
    up_line="$(grep -nF '}" up -d' <<< "$body" | tail -1 | cut -d: -f1)"
    wait_line="$(grep -n '_ods_cli_wait_for_litellm_ready' <<< "$body" | head -1 | cut -d: -f1)"
    [[ -n "$up_line" ]] || fail "$command has no Compose up call to order against"
    (( wait_line > up_line )) || fail "$command waits before Compose starts the stack"
done

echo "PASS: start and restart wait for LiteLLM readiness; slow gateways warn; stacks without it skip"
