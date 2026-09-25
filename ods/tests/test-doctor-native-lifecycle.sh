#!/usr/bin/env bash
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
eval "$(extract _doctor_check_llama_server)"
eval "$(extract collect_extension_diagnostics)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
log_ok() { :; }
log_fail() { :; }
log_info() { :; }
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
ROOT_DIR="$root"
declare -A SERVICE_PORTS=([llama-server]=11434) SERVICE_HEALTH=([llama-server]=/health)
platform=Darwin
uname() { printf '%s\n' "$platform"; }
curl_rc=0
curl_calls=()
curl() { curl_calls+=("${*: -1}"); return "$curl_rc"; }
docker_mode=missing
docker_calls="$fixture/docker.calls"
mock_container_state=exited
exit_state='0 false'
docker() {
    printf '%s\n' "$*" >>"$docker_calls"
    case "$1" in
        ps) [[ "$docker_mode" == running ]] && printf 'ods-llama-server\n'; return 0 ;;
        inspect)
            [[ "$docker_mode" != missing ]] || return 1
            case "$3" in
                '{{.State.Status}}') printf '%s\n' "$mock_container_state" ;;
                '{{.State.ExitCode}} {{.State.OOMKilled}}') printf '%s\n' "$exit_state" ;;
                *) return 1 ;;
            esac ;;
        *) return 1 ;;
    esac
}
sr_container() { printf 'ods-llama-server\n'; }
DOCKER_DAEMON=false
OLLAMA_PORT=11434
unset ODS_NATIVE_LLAMA_PORT BIND_ADDRESS
_doctor_check_llama_server
[[ "$LLM_STATUS" == ok && "$LLM_URL" == http://127.0.0.1:8080 && ! -e "$docker_calls" ]] \
    || fail 'native model must not require Docker or use OLLAMA_PORT'
ODS_NATIVE_LLAMA_PORT=18080
for pair in '0.0.0.0|127.0.0.1' '::|[::1]' '::1|[::1]' '127.0.0.2|127.0.0.2'; do
    BIND_ADDRESS="${pair%%|*}"
    _doctor_check_llama_server
    [[ "$LLM_STATUS" == ok && "$LLM_URL" == "http://${pair#*|}:18080" ]] \
        || fail "incorrect native bind/port: $pair"
    [[ "${curl_calls[-1]}" == "$LLM_URL/health" ]] || fail 'wrong native health URL'
done
curl_rc=22
_doctor_check_llama_server
[[ "$LLM_STATUS" == fail && -n "$LLM_RECOVERY" ]] || fail 'dead native endpoint must fail'
curl_rc=0
for invalid in 0 65536 bad 999999999999999999999; do
    ODS_NATIVE_LLAMA_PORT="$invalid"
    count=${#curl_calls[@]}
    _doctor_check_llama_server
    [[ "$LLM_STATUS" == fail && ${#curl_calls[@]} == "$count" ]] || fail 'invalid port was probed'
done
platform=Linux
DOCKER_DAEMON=true
docker_mode=missing
_doctor_check_llama_server
[[ "$LLM_STATUS" == fail ]] || fail 'Linux still requires a running managed container'
docker_mode=running
_doctor_check_llama_server
[[ "$LLM_STATUS" == ok && "$LLM_URL" == http://127.0.0.1:11434 ]] || fail 'Linux port changed'
curl_rc=22
_doctor_check_llama_server
[[ "$LLM_STATUS" == fail ]] || fail 'unhealthy Linux container must fail'
printf '[PASS] native/default/custom/bind/failure probes and Linux behavior\n'

ROOT_DIR="$fixture/install"
mkdir -p "$ROOT_DIR/data/extension-progress" "$ROOT_DIR/tool"
touch "$ROOT_DIR/tool/compose.yaml"
declare -a SERVICE_IDS=(tool)
declare -A SERVICE_CATEGORIES=([tool]=optional) SERVICE_COMPOSE=([tool]="$ROOT_DIR/tool/compose.yaml")
declare -A SERVICE_CONTAINERS=([tool]=ods-tool) SERVICE_STARTUP_CHECKS=([tool]=false)
declare -A SERVICE_SOCKET_ONLY=([tool]=0) SERVICE_DEPENDS=() SERVICE_GPU_BACKENDS=()
SERVICE_PORTS[tool]=0
SERVICE_HEALTH[tool]=''
GPU_BACKEND=apple
receipt="$ROOT_DIR/data/extension-progress/tool.json"
for fault in none nonzero oom no-receipt malformed wrong-id unverified pending daemon port socket missing; do
    docker_mode=present
    mock_container_state=exited
    exit_state='0 false'
    SERVICE_PORTS[tool]=0
    SERVICE_STARTUP_CHECKS[tool]=false
    SERVICE_SOCKET_ONLY[tool]=0
    printf '%s\n' '{"service_id":"tool","status":"started","exit_verified":true}' >"$receipt"
    case "$fault" in
        nonzero) exit_state='1 false' ;;
        oom) exit_state='0 true' ;;
        no-receipt) rm "$receipt" ;;
        malformed) printf '{' >"$receipt" ;;
        wrong-id) printf '%s\n' '{"service_id":"other","status":"started","exit_verified":true}' >"$receipt" ;;
        unverified) printf '%s\n' '{"service_id":"tool","status":"started","exit_verified":false}' >"$receipt" ;;
        pending) printf '%s\n' '{"service_id":"tool","status":"starting","exit_verified":true}' >"$receipt" ;;
        daemon) SERVICE_STARTUP_CHECKS[tool]=true ;;
        port) SERVICE_PORTS[tool]=8000 ;;
        socket) SERVICE_SOCKET_ONLY[tool]=1 ;;
        missing) docker_mode=missing ;;
    esac
    result=$(collect_extension_diagnostics)
    if [[ "$fault" == none ]]; then
        jq -e '.[0].health_status == "completed" and .[0].container_state == "exited" and .[0].issues == []' \
            <<<"$result" >/dev/null || fail 'verified CLI completion was treated as a stopped daemon'
    else
        jq -e '.[0].issues | index("container_not_running") != null' <<<"$result" >/dev/null \
            || fail "unproven or failed completion was hidden: $fault"
    fi
done
printf '[PASS] CLI completion requires receipt, matching id, zero exit and no OOM; daemon checks retained\n'
