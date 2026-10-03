#!/usr/bin/env bash
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
eval "$(extract_function _doctor_check_external_llm)"
eval "$(extract_function _doctor_check_llm_backend)"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
log_ok() { :; }
log_fail() { :; }
log_info() { :; }
log_warn() { :; }

curl_calls=0
curl_rc=0
curl_args=()
curl() {
    curl_calls=$((curl_calls + 1))
    curl_args=("$@")
    if [[ " ${curl_args[*]} " == *' Authorization: Bearer sk-fixture-not-real '* ]]; then
        return 0
    fi
    return "$curl_rc"
}
local_checks=0
_doctor_check_llama_server() {
    local_checks=$((local_checks + 1))
    LLM_STATUS=local
}

DOCKER_DAEMON=false
EXTERNAL_LLM_URL=
ODS_MODE=lemonade
LLM_BACKEND=lemonade
LEMONADE_EXTERNAL=true
LEMONADE_BASE_URL=http://127.0.0.1:8080
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=Qwen3.6-35B-A3B-GGUF
LEMONADE_API_KEY=sk-fixture-not-real
curl_rc=22
_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$LLM_PROVIDER" == lemonade && "$LLM_MODEL" == "$LEMONADE_MODEL" ]] \
    || fail 'external Lemonade must be reported as the active, healthy LLM backend'
[[ "$curl_calls" == 2 && "$local_checks" == 0 ]] \
    || fail 'doctor must retry a protected external Lemonade route with its configured key'
[[ " ${curl_args[*]} " == *' http://127.0.0.1:8080/api/v1/models '* ]] \
    || fail 'doctor must probe the versioned Lemonade models endpoint'
[[ " ${curl_args[*]} " == *' Authorization: Bearer sk-fixture-not-real '* ]] \
    || fail 'doctor must authenticate a protected Lemonade endpoint'

unset LEMONADE_API_KEY
unset LEMONADE_ADMIN_API_KEY LITELLM_LEMONADE_API_KEY
curl_rc=0
before_calls="$curl_calls"
_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$curl_calls" == "$((before_calls + 1))" ]] \
    || fail 'unprotected Lemonade must succeed without a fabricated credential'

curl_rc=22
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_PROVIDER" == lemonade && "$local_checks" == 0 ]] \
    || fail 'unreachable Lemonade must fail, not fall back to absent llama-server'

LEMONADE_BASE_URL=
before_calls="$curl_calls"
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_PROVIDER" == lemonade && "$curl_calls" == "$before_calls" ]] \
    || fail 'missing external host endpoint must fail closed without a localhost probe'

LEMONADE_EXTERNAL=false
ODS_MODE=local
LLM_BACKEND=llama-server
_doctor_check_llm_backend
[[ "$LLM_STATUS" == local && "$local_checks" == 1 ]] \
    || fail 'managed local installs must retain the llama-server diagnostic'

printf '[OK] doctor checks the configured external Lemonade route, not absent local llama-server\n'
