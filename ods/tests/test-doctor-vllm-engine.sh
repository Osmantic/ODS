#!/usr/bin/env bash
# ods doctor on a vLLM install (LLM_BACKEND=vllm, docker-compose.vllm.yml):
# vllm-server has no host port and llama-server is off, so the LLM check reads
# Docker's health of ods-vllm-server. A cold start (weights load plus kernel
# compiles, up to 15 minutes) reads as starting, not a fault; a missing
# llama-server is no fault; a llama-server container an earlier install left
# running is named, with a stop command that does not stop vLLM.
# Extracted production functions consume the fixture globals below.
# shellcheck disable=SC2034
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if (( BASH_VERSINFO[0] < 4 )); then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$candidate" ]]; then exec "$candidate" "$0" "$@"; fi
    done
    echo 'Bash 4+ is required' >&2
    exit 1
fi
extract() {
    awk -v name="$1" '$0 ~ "^" name "[(][)]" { active=1 }
        active {print} active && /^}/ {exit}' "$root/scripts/ods-doctor.sh"
}
eval "$(extract _doctor_check_vllm_engine)"
eval "$(extract _doctor_check_llm_backend)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }
messages=()
log_ok() { messages+=("ok:$*"); }
log_fail() { messages+=("fail:$*"); }
log_info() { messages+=("info:$*"); }
log_warn() { messages+=("warn:$*"); }
_doctor_check_llama_server() { LLM_PROVIDER="llama-server"; LLM_STATUS="llama-checked"; }
ROOT_DIR="$root"
vllm_state="running healthy"
llama_running=false
docker() {
    case "$1" in
        inspect)
            [[ "${*: -1}" == ods-vllm-server ]] || fail "inspected ${*: -1}"
            [[ "$vllm_state" != missing ]] || { printf 'Error: No such object: ods-vllm-server\n'; return 1; }
            printf '%s\n' "$vllm_state" ;;
        ps) [[ "$llama_running" == false ]] || printf 'ods-llama-server\n' ;;
        *) fail "unexpected docker $*" ;;
    esac
}
DOCKER_DAEMON=true
ODS_MODE=local
VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4
unset EXTERNAL_LLM_URL NATIVE_LLM_BASE_URL

for backend in vllm VLLM; do
    LLM_BACKEND="$backend"
    _doctor_check_llm_backend
    [[ "$LLM_PROVIDER" == vLLM && "$LLM_STATUS" == ok && "$LLM_MODEL" == GLM-5.3-Flash-NVFP4 ]] \
        || fail "LLM_BACKEND=$backend did not check the vLLM engine: $LLM_PROVIDER $LLM_STATUS"
done
pass 'LLM_BACKEND=vllm checks vllm-server, never llama-server'

messages=()
vllm_state="running starting"
_doctor_check_llm_backend
[[ "$LLM_STATUS" == starting && -z "$LLM_RECOVERY" ]] || fail "a cold start was not starting: $LLM_STATUS"
said="$(printf '%s\n' "${messages[@]}")"
grep -q '^info:LLM backend: vLLM (local) — starting: a first start loads the model' <<<"$said" \
    || fail "cold start message: ${messages[*]}"
if grep -q '^fail:' <<<"$said"; then fail "a cold start reported a fault: ${messages[*]}"; fi
pass 'a cold start reads as starting, with no fault and no restart advice'

for state in 'running unhealthy:ods restart vllm-server' 'exited none:ods start vllm-server' \
    'missing:ods start vllm-server'; do
    vllm_state="${state%%:*}"
    _doctor_check_llm_backend
    [[ "$LLM_STATUS" == fail && "$LLM_RECOVERY" == "${state##*:}" ]] \
        || fail "'$vllm_state' should fail with ${state##*:}: $LLM_STATUS $LLM_RECOVERY"
done
pass 'an unhealthy or stopped engine fails with a vllm-server recovery'

messages=()
vllm_state="running healthy"
llama_running=true
_doctor_check_llm_backend
[[ "$LLM_STATUS" == ok && "$LLM_LOCAL_WARNING" == true ]] || fail 'a running stale llama-server was not noted'
said="$(printf '%s\n' "${messages[@]}")"
grep -q 'docker stop ods-llama-server' <<<"$said" || fail "stale llama-server note: ${messages[*]}"
grep -q 'Stop it with: docker stop ods-llama-server' "$root/scripts/ods-doctor.sh" \
    || fail 'the fix hint must stop the container, since ods stop llama-server names vllm-server on this route'
pass 'a llama-server left running beside vLLM is named with a stop that keeps vLLM'

LLM_BACKEND=llama-server
llama_running=false
_doctor_check_llm_backend
[[ "$LLM_STATUS" == llama-checked ]] || fail 'a llama-server install left the llama-server check'
LLM_BACKEND=vllm
ODS_MODE=cloud
_doctor_check_llm_backend
[[ "$LLM_PROVIDER" != vLLM ]] || fail 'cloud mode checked the vLLM engine'
pass 'llama-server and cloud installs keep their checks'
