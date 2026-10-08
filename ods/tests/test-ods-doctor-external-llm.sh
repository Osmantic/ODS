#!/usr/bin/env bash
# In API mode, ods doctor probes the API with the key the installer stored
# (a keyed API answers 401 without it, which is not "down"), passes the key
# only as a header file, and tells a refused or missing key apart from an
# unreachable API. Cloud mode keeps its plain probe and never sends the key.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$root/scripts/ods-doctor.sh"
extract_function() {
    awk -v signature="^$1[(][)]" '
        $0 ~ signature { in_block = 1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$source_file"
}
for name in _doctor_check_external_llm _doctor_check_llm_backend; do
    body="$(extract_function "$name")"
    [[ -n "$body" ]] || { printf '[FAIL] %s not found in ods-doctor.sh\n' "$name" >&2; exit 1; }
    eval "$body"
done

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
log_ok() { :; }
log_fail() { :; }
log_info() { :; }
log_warn() { :; }

ROOT_DIR="$(mktemp -d)"
trap 'rm -rf -- "$ROOT_DIR"' EXIT
mkdir -p "$ROOT_DIR/config/litellm"
key_file="$ROOT_DIR/config/litellm/external-upstream.key"
key="sk-doctor-fixture-$(printf 'k%.0s' {1..20})"

# The fake curl answers with $mock_status and records what it was given in
# files: the doctor runs it inside $(...), so variables would not survive.
mock_status=200
mock_rc=0
curl() {
    local arg previous=""
    printf '%s' "$*" > "$ROOT_DIR/seen-args"
    : > "$ROOT_DIR/seen-header"
    for arg in "$@"; do
        if [[ "$previous" == -H && "$arg" == @* ]]; then
            IFS= read -r line < "${arg#@}" || true
            printf '%s' "$line" > "$ROOT_DIR/seen-header"
        fi
        previous="$arg"
    done
    if [[ " $* " == *" -w "* ]]; then
        printf '%s' "$mock_status"
    elif [[ "$mock_status" != 2* ]]; then
        return 22
    fi
    return "$mock_rc"
}

DOCKER_DAEMON=false
NATIVE_LLM_BASE_URL=
EXTERNAL_LLM_URL=https://api.example.test
EXTERNAL_LLM_PROVIDER=openai-compatible
EXTERNAL_LLM_MODEL=fixture-model
ODS_MODE=local

printf '%s\n' "$key" > "$key_file"
mock_status=200
_doctor_check_llm_backend
seen_args="$(cat "$ROOT_DIR/seen-args")"; seen_header="$(cat "$ROOT_DIR/seen-header")"
[[ "$LLM_STATUS" == ok && -z "$LLM_FAILURE" ]] || fail "a working keyed API was not reported healthy ($LLM_STATUS|$LLM_FAILURE)"
[[ "$seen_header" == "Authorization: Bearer $key" ]] || fail "the stored key was not sent as a header file"
[[ "$seen_args" != *"$key"* ]] || fail "the key reached curl's arguments"
[[ "$seen_args" == *"https://api.example.test/v1/models"* ]] || fail "the probe did not target the API's model list ($seen_args)"

mock_status=401
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_FAILURE" == key-refused && "$LLM_RECOVERY" == *"--external-llm-key-file"* ]] \
    || fail "a refused key was not reported as one ($LLM_STATUS|$LLM_FAILURE|$LLM_RECOVERY)"

: > "$key_file"
mock_status=401
_doctor_check_llm_backend
seen_header="$(cat "$ROOT_DIR/seen-header")"
[[ "$LLM_STATUS" == fail && "$LLM_FAILURE" == key-required && -z "$seen_header" ]] \
    || fail "a keyed API with no stored key was not reported as needing one ($LLM_STATUS|$LLM_FAILURE)"

mock_status=000
mock_rc=7
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_FAILURE" == unreachable && "$LLM_RECOVERY" == *connectivity* ]] \
    || fail "an unreachable API was not reported as unreachable ($LLM_STATUS|$LLM_FAILURE|$LLM_RECOVERY)"
mock_rc=0

# Cloud mode probes its own gateway and must never receive the API key.
printf '%s\n' "$key" > "$key_file"
EXTERNAL_LLM_URL=
ODS_MODE=cloud
LLM_API_URL=http://litellm:4000
mock_status=200
_doctor_check_llm_backend
seen_args="$(cat "$ROOT_DIR/seen-args")"; seen_header="$(cat "$ROOT_DIR/seen-header")"
[[ "$LLM_STATUS" == ok && -z "$seen_header" && "$seen_args" != *"$key"* && "$seen_args" == *"-sf"* ]] \
    || fail "cloud mode changed its probe or sent the API key ($LLM_STATUS|$seen_args)"

printf '[OK] doctor probes a keyed API with its stored key and names key problems\n'
