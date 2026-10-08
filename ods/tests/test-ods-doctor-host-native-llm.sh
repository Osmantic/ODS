#!/usr/bin/env bash
# ods doctor checks the llama-server the Windows Portal runs outside the stack
# (NATIVE_LLM_BASE_URL): it probes that server's /health without a key and
# never falls back to the absent in-stack llama-server.
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
for name in _doctor_check_host_native_llm _doctor_check_external_llm _doctor_check_llm_backend; do
    body="$(extract_function "$name")"
    [[ -n "$body" ]] || { printf '[FAIL] %s not found in ods-doctor.sh\n' "$name" >&2; exit 1; }
    eval "$body"
done

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
    return "$curl_rc"
}
local_checks=0
_doctor_check_llama_server() {
    local_checks=$((local_checks + 1))
    LLM_STATUS=local
}

DOCKER_DAEMON=false
EXTERNAL_LLM_URL=
ODS_MODE=local
LLM_BACKEND=llama-server
NATIVE_LLM_BASE_URL=http://127.0.0.1:8080
GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
LLAMA_SERVER_API_KEY=abababababababababababababababab

_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$LLM_PROVIDER" == "llama-server (Windows)" && "$LLM_MODEL" == "$GGUF_FILE" ]] \
    || fail "the host-native llama-server must be reported as the active, healthy LLM backend ($LLM_STATUS|$LLM_PROVIDER|$LLM_MODEL)"
[[ "$curl_calls" == 1 && "$local_checks" == 0 ]] \
    || fail 'doctor must probe the host-native server once and skip the in-stack llama-server'
[[ " ${curl_args[*]} " == *' http://127.0.0.1:8080/health '* ]] \
    || fail "doctor must probe the native server's /health (${curl_args[*]})"
[[ " ${curl_args[*]} " != *"$LLAMA_SERVER_API_KEY"* && " ${curl_args[*]} " != *Authorization* ]] \
    || fail 'the /health probe needs no key and must not carry one'

curl_rc=7
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_PROVIDER" == "llama-server (Windows)" && "$local_checks" == 0 ]] \
    || fail 'an unreachable host-native server must fail, not fall back to the absent in-stack llama-server'
[[ "$LLM_RECOVERY" == *"ODS Portal"* ]] \
    || fail "recovery must point at the Portal that owns the Windows task ($LLM_RECOVERY)"

# The Windows Portal's route goes through model-router: WSL's NAT networking
# cannot reach Windows loopback, so the doctor probes from that container.
docker_rc=0
docker_args=()
docker() {
    docker_args=("$@")
    return "$docker_rc"
}
ODS_HOST_LLM_TRANSPORT=model-router
NATIVE_LLM_CONTAINER_BASE_URL=http://host.docker.internal:8080
DOCKER_DAEMON=true
curl_rc=7
before_calls="$curl_calls"
_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$LLM_URL" == http://host.docker.internal:8080 && "$curl_calls" == "$before_calls" ]] \
    || fail "the model-router route must be probed from the container, not WSL loopback ($LLM_STATUS|$LLM_URL|$curl_calls)"
[[ "${docker_args[0]}" == exec && "${docker_args[1]}" == ods-model-router \
   && "${docker_args[${#docker_args[@]}-1]}" == http://host.docker.internal:8080 ]] \
    || fail "the probe must run in ods-model-router against the container URL (${docker_args[*]})"
[[ " ${docker_args[*]} " != *"$LLAMA_SERVER_API_KEY"* ]] \
    || fail 'the container /health probe needs no key and must not carry one'
docker_rc=1
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "$LLM_RECOVERY" == *"ods start"* && "$LLM_RECOVERY" == *"ODS Portal"* ]] \
    || fail "an unreachable model-router route must fail and name both remedies ($LLM_RECOVERY)"
DOCKER_DAEMON=false
docker_args=()
_doctor_check_llm_backend
[[ "$LLM_STATUS" == fail && "${#docker_args[@]}" == 0 ]] \
    || fail 'without Docker the model-router route must fail without probing'
ODS_HOST_LLM_TRANSPORT=direct
curl_rc=0
_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$LLM_URL" == http://127.0.0.1:8080 ]] \
    || fail "the direct transport keeps the host probe ($LLM_STATUS|$LLM_URL)"

NATIVE_LLM_BASE_URL=
_doctor_check_llm_backend
[[ "$LLM_STATUS" == local && "$local_checks" == 1 ]] \
    || fail 'managed local installs must retain the llama-server diagnostic'

# The retired Lemonade branch is gone: an unmigrated .env gets the in-stack
# llama-server diagnostic (and the inference-contract blocker), never a
# Lemonade probe.
ODS_MODE=lemonade
LLM_BACKEND=lemonade
LEMONADE_EXTERNAL=true
LEMONADE_BASE_URL=http://127.0.0.1:13305
before_calls="$curl_calls"
_doctor_check_llm_backend
[[ "$LLM_STATUS" == local && "$local_checks" == 2 && "$curl_calls" == "$before_calls" ]] \
    || fail 'a Lemonade-era .env must not be probed as a Lemonade server'

printf '[OK] doctor checks the host-native llama-server, not absent local llama-server or Lemonade\n'
