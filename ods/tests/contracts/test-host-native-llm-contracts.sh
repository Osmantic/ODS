#!/usr/bin/env bash
# Host-native llama-server contracts: the Windows Portal runs llama-server.exe
# on Windows (NATIVE_LLM_BASE_URL) while this stack runs in WSL. The stack
# keeps its own llama-server off, reaches the native server through LiteLLM
# and model-router with LLAMA_SERVER_API_KEY, and proves a real completion.
# Replaces the external-Lemonade contracts this route had before round F.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
PYTHON_CMD="${ODS_PYTHON_CMD:-python3}"

fail() { echo "[FAIL] $*"; exit 1; }

echo "[contract] host-native overlay keeps llama-server off and gives only the gateways the key"
[[ -f docker-compose.host-native-llm.yml ]] || fail "docker-compose.host-native-llm.yml missing"
"$PYTHON_CMD" - <<'PY' || fail "docker-compose.host-native-llm.yml contract"
import sys
import yaml

data = yaml.safe_load(open("docker-compose.host-native-llm.yml", encoding="utf-8"))
services = data["services"]
errors = []
if services["llama-server"].get("profiles") != ["local-inference"]:
    errors.append("llama-server must be profiled out")
for name in ("litellm", "model-router"):
    env = services[name].get("environment", [])
    if "LLAMA_SERVER_API_KEY=${LLAMA_SERVER_API_KEY:-}" not in env:
        errors.append(f"{name} must receive LLAMA_SERVER_API_KEY")
    if "host.docker.internal:host-gateway" not in services[name].get("extra_hosts", []):
        errors.append(f"{name} must reach the host gateway")
for name, service in services.items():
    if name in {"litellm", "model-router"}:
        continue
    if "LLAMA_SERVER_API_KEY" in str(service.get("environment", "")):
        errors.append(f"{name} must not receive the native server's key")
dashboard = services["dashboard-api"]["environment"]
for line in ("LLM_BACKEND=llama-server", "LLM_API_BASE_PATH=/v1",
             "ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}"):
    if line not in dashboard:
        errors.append(f"dashboard-api must set {line}")
if "lemonade" in open("docker-compose.host-native-llm.yml", encoding="utf-8").read().lower():
    errors.append("the overlay must not mention Lemonade")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

echo "[contract] schema and example document the host-native keys"
for key in NATIVE_LLM_BASE_URL NATIVE_LLM_CONTAINER_BASE_URL ODS_HOST_LLM_TRANSPORT LLAMA_SERVER_API_KEY; do
    jq -e --arg key "$key" '.properties[$key]' .env.schema.json >/dev/null \
        || fail ".env.schema.json missing $key"
    grep -q "$key=" .env.example || fail ".env.example missing $key"
done
jq -e '.properties.AMD_INFERENCE_RUNTIME_MODE.enum | index("windows-portal-llama-server")' .env.schema.json >/dev/null \
    || fail ".env.schema.json must allow AMD_INFERENCE_RUNTIME_MODE=windows-portal-llama-server"

echo "[contract] explicit LAN binding overrides stale env during reinstall"
grep -q 'BIND_ADDRESS_EXPLICIT' install-core.sh || fail "install-core must track explicit --lan/BIND_ADDRESS"
grep -q 'BIND_ADDRESS_EXPLICIT' installers/phases/06-directories.sh \
    || fail "phase 06 must let explicit BIND_ADDRESS override stale .env"

echo "[contract] host-native installs pull no managed inference image"
phase08_plan="$({
    SCRIPT_DIR="$ROOT_DIR" \
    LOG_FILE="${TMPDIR:-/tmp}/ods-host-native-images.log" \
    DRY_RUN=true GPU_BACKEND=cpu NATIVE_LLM_BASE_URL=http://localhost:8080 \
    ENABLE_COMFYUI=false ENABLE_VOICE=false ENABLE_WORKFLOWS=false \
    ENABLE_RAG=false ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false \
    ENABLE_HERMES=false COMPOSE_FLAGS='' \
    bash -c '
        ods_progress() { :; }; show_phase() { :; }; ai() { :; }
        ai_ok() { :; }; ai_warn() { :; }; bootline() { :; }; signal() { :; }
        source "$SCRIPT_DIR/installers/phases/08-images.sh"
        printf "%s\n" "${PULL_LIST[@]}"
    '
} 2>/dev/null)"
if grep -q 'LLAMA-SERVER' <<<"$phase08_plan"; then
    fail "host-native image plan still pulls the in-stack llama-server: $phase08_plan"
fi

echo "[contract] host-native install verifies a real completion and reports failures honestly"
health_functions="$(awk '
    /^_phase12_env_get\(\)/ { emit=1 }
    /^_phase12_verify_external_llm_completion\(\)/ { emit=0 }
    emit { print }
' installers/phases/12-health.sh)"
[[ "$health_functions" == *'_phase12_verify_host_native_llm_completion()'* ]] \
    || fail "phase 12 must verify a real host-native completion"
[[ "$health_functions" == *'-w '\''%{http_code}'\'''* ]] \
    || fail "phase 12 host-native completion must capture the HTTP status"
[[ "$health_functions" == *'"chat_template_kwargs":{"enable_thinking":false}'* ]] \
    || fail "phase 12 host-native readiness must disable reasoning-token exhaustion"
if grep -qi 'lemonade' <<<"$health_functions"; then
    fail "phase 12 health helpers must not mention Lemonade"
fi

declare -A SERVICE_PORTS=([litellm]=4000)
INSTALL_DIR="${TMPDIR:-/tmp}/ods-host-native-health-test"
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/ods-host-native-health.XXXXXX")"
RED='' BGRN='' NC=''
GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
NATIVE_LLM_BASE_URL=http://localhost:8080
ai() { :; }
ai_warn() { printf 'WARN:%s\n' "$*"; }
eval "$health_functions"

STUB_CURL_STATUS=503
STUB_CURL_RC=0
STUB_CURL_BODY='{"error":{"message":"No verified active model route is available yet","code":503}}'
curl() {
    local output_file=""
    while (( $# > 0 )); do
        case "$1" in
            -o) output_file="$2"; shift 2 ;;
            -w) shift 2 ;;
            *) shift ;;
        esac
    done
    printf '%s' "$STUB_CURL_BODY" > "$output_file"
    printf '%s' "$STUB_CURL_STATUS"
    return "$STUB_CURL_RC"
}

set +e
health_output="$(_phase12_verify_host_native_llm_completion 2>&1)"
health_rc=$?
set -e
[[ "$health_rc" -ne 0 ]] || fail "phase 12 accepted a LiteLLM HTTP 503"
[[ "$health_output" == *'returned HTTP 503'* ]] || fail "phase 12 did not surface the LiteLLM HTTP 503"
[[ "$health_output" != *'returned no assistant content'* ]] \
    || fail "phase 12 mislabeled an HTTP 503 as empty assistant content"
[[ "$health_output" == *'ODS Portal'* ]] || fail "phase 12 recovery must point at the Portal that owns the runtime"
grep -q 'No verified active model route is available yet' "$LOG_FILE" \
    || fail "phase 12 did not retain the bounded HTTP error for diagnosis"

STUB_CURL_STATUS=000
STUB_CURL_RC=28
STUB_CURL_BODY=''
set +e
transport_output="$(_phase12_verify_host_native_llm_completion 2>&1)"
transport_rc=$?
set -e
[[ "$transport_rc" -ne 0 ]] || fail "phase 12 accepted a failed transport"
[[ "$transport_output" == *'curl exit 28'* ]] || fail "phase 12 did not identify the curl transport failure"
[[ "$transport_output" == *'http://localhost:8080/health'* ]] \
    || fail "phase 12 must show how to probe the native server"

STUB_CURL_STATUS=200
STUB_CURL_RC=0
STUB_CURL_BODY='{"choices":[{"message":{"content":"OK"}}]}'
success_output="$(_phase12_verify_host_native_llm_completion 2>&1)" \
    || fail "phase 12 rejected a valid host-native completion: $success_output"
[[ "$success_output" == *'completion route healthy'* ]] \
    || fail "phase 12 did not report the valid completion as healthy"
rm -f -- "$LOG_FILE"
unset -f curl

echo "[contract] preflight checks LiteLLM for the host-native route"
grep -q 'ods_preflight_host_native_llm()' lib/preflight-llm-route.sh \
    || fail "preflight must detect the host-native route"
grep -q 'if ods_preflight_uses_litellm; then' ods-preflight.sh \
    || fail "ods-preflight must select the LiteLLM route"

echo "[contract] doctor diagnoses a host-native route without its key"
grep -q 'ODS-RUNTIME-HOST-NATIVE-KEY-MISSING' scripts/ods-doctor.sh \
    || fail "ods-doctor must warn when the host-native route has no LLAMA_SERVER_API_KEY"

echo "[contract] resolver selects the host-native overlay instead of cloud or a hardware overlay"
resolved="$(env -u AMD_INFERENCE_BACKEND NATIVE_LLM_BASE_URL=http://localhost:8080 \
    ./scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --ods-mode local --gpu-backend amd --tier SH_LARGE --env)"
grep -q 'docker-compose.host-native-llm.yml' <<<"$resolved" || fail "host-native overlay missing from the resolved stack"
for overlay in docker-compose.cloud.yml docker-compose.amd.yml compose.local.yaml; do
    if grep -q "$overlay" <<<"$resolved"; then
        fail "the host-native stack must not include $overlay"
    fi
done
grep -Eq 'extensions[\\/]+services[\\/]+litellm[\\/]+compose.yaml' <<<"$resolved" \
    || fail "the host-native stack must keep the LiteLLM gateway"

echo "[contract] installer hardware profiles cannot override the host-native route"
for backend in cpu amd nvidia; do
    installer_resolved="$(
        export SCRIPT_DIR="$ROOT_DIR" TIER=1 GPU_BACKEND="$backend" GPU_COUNT=1 ODS_MODE=local
        export CAP_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.${backend}.yml"
        export NATIVE_LLM_BASE_URL=http://localhost:8080
        unset AMD_INFERENCE_BACKEND
        LOG_FILE=/dev/null
        log() { :; }
        source installers/lib/compose-select.sh
        resolve_compose_config
        printf '%s\n' "$COMPOSE_FLAGS"
    )"
    [[ "$installer_resolved" != *docker-compose.cloud.yml* \
       && "$installer_resolved" == *docker-compose.host-native-llm.yml* \
       && "$installer_resolved" != *"docker-compose.${backend}.yml"* \
       && "$installer_resolved" != *compose.local.yaml* ]] \
        || fail "$backend profile overrode the host-native selection: $installer_resolved"
done

echo "[contract] managed AMD keeps its hardware profile"
managed_resolved="$(env -u NATIVE_LLM_BASE_URL -u AMD_INFERENCE_BACKEND \
    ./scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" \
    --ods-mode local --gpu-backend amd --tier SH_LARGE \
    --profile-overlays docker-compose.base.yml,docker-compose.amd.yml --env)"
[[ "$managed_resolved" == *docker-compose.amd.yml* \
   && "$managed_resolved" != *docker-compose.host-native-llm.yml* ]] \
    || fail "managed AMD lost its hardware profile"

echo "[contract] installer scopes firewall access for the host-native server"
grep -q '_phase11_allow_host_native_llm_firewall ods-network' installers/phases/11-services.sh \
    || fail "phase 11 must allow container-to-host access to the native server"
grep -q 'ods-native-llm' installers/phases/11-services.sh || fail "the native server firewall rule must be labeled"

echo "[contract] CLI invalidates stale compose flags around the host-native route"
grep -q 'docker-compose.host-native-llm.yml' ods-cli || fail "ods-cli must recognize the host-native compose cache state"
grep -q '_host_native_active' ods-cli || fail "ods-cli must tell host-native installs apart"
grep -q 'docker-compose.lemonade-external.yml' ods-cli \
    || fail "ods-cli must still invalidate a cached retired Lemonade overlay"

echo "[PASS] host-native llama-server contracts"
