#!/usr/bin/env bash
# External Lemonade SDK runtime contract tests.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
PYTHON_CMD="${ODS_PYTHON_CMD:-python3}"

echo "[contract] external Lemonade compose overlay exists"
[[ -f docker-compose.lemonade-external.yml ]] \
  || { echo "[FAIL] docker-compose.lemonade-external.yml missing"; exit 1; }

echo "[contract] schema documents external Lemonade env"
for key in LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_CONTAINER_BASE_URL LEMONADE_API_BASE_PATH LEMONADE_MODEL; do
  grep -q "\"$key\"" .env.schema.json \
    || { echo "[FAIL] .env.schema.json missing $key"; exit 1; }
  grep -q "^$key=" .env.example \
    || { echo "[FAIL] .env.example missing $key"; exit 1; }
done
grep -q '"external-lemonade"' .env.schema.json \
  || { echo "[FAIL] .env.schema.json must allow AMD_INFERENCE_RUNTIME_MODE=external-lemonade"; exit 1; }

echo "[contract] renderer supports external Lemonade model and endpoint"
rendered="$("$PYTHON_CMD" scripts/render-runtime-configs.py \
  --surface litellm-lemonade \
  --ods-mode lemonade \
  --gpu-backend amd \
  --lemonade-model-id Qwen3-0.6B-GGUF \
  --lemonade-api-base http://host.docker.internal:13305/api/v1)"
grep -q 'openai/Qwen3-0.6B-GGUF' <<<"$rendered" \
  || { echo "[FAIL] renderer must use supplied Lemonade model id"; exit 1; }
grep -q 'host.docker.internal:13305/api/v1' <<<"$rendered" \
  || { echo "[FAIL] renderer must use supplied Lemonade API base"; exit 1; }

echo "[contract] external Lemonade ODS Talk timeout is long enough for full models"
grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' docker-compose.lemonade-external.yml \
  || { echo "[FAIL] external Lemonade overlay must set ODS_TALK_HERMES_TIMEOUT=900"; exit 1; }

echo "[contract] installer discovers the best available external Lemonade chat model"
grep -q '_phase06_discover_lemonade_model' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must discover the model served by external Lemonade"; exit 1; }
grep -q 'select-external-lemonade-model.py' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must use the behavioral external Lemonade model selector"; exit 1; }
"$PYTHON_CMD" tests/test-external-lemonade-model-selector.py \
  || { echo "[FAIL] external Lemonade model selector behavioral tests failed"; exit 1; }
if grep -q 'LLM_MODEL_VALUE' installers/phases/06-directories.sh; then
  echo "[FAIL] phase 06 must not reference undefined LLM_MODEL_VALUE"
  exit 1
fi
grep -q 'LEMONADE_MODEL_VALUE' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must write a resolved LEMONADE_MODEL value"; exit 1; }
grep -q '_env_get_explicit_first LEMONADE_MODEL' installers/phases/06-directories.sh \
  || { echo "[FAIL] explicit LEMONADE_MODEL must override stale .env values during reinstall"; exit 1; }
grep -q '_env_get_explicit_first LEMONADE_BASE_URL' installers/phases/06-directories.sh \
  || { echo "[FAIL] explicit LEMONADE_BASE_URL/--lemonade-url must override stale .env values during reinstall"; exit 1; }

echo "[contract] explicit LAN binding overrides stale env during reinstall"
grep -q 'BIND_ADDRESS_EXPLICIT' install-core.sh \
  || { echo "[FAIL] install-core must track explicit --lan/BIND_ADDRESS"; exit 1; }
grep -q 'BIND_ADDRESS_EXPLICIT' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must let explicit BIND_ADDRESS override stale .env"; exit 1; }

echo "[contract] external Lemonade does not pull managed Lemonade image"
grep -q '_lemonade_external' installers/phases/08-images.sh \
  || { echo "[FAIL] phase 08 must skip managed Lemonade image pulls in external mode"; exit 1; }

echo "[contract] external Lemonade skips every managed inference image"
phase08_plan="$({
  SCRIPT_DIR="$ROOT_DIR" \
  LOG_FILE="${TMPDIR:-/tmp}/ods-external-lemonade-images.log" \
  DRY_RUN=true GPU_BACKEND=cpu LEMONADE_EXTERNAL=true \
  ENABLE_COMFYUI=false ENABLE_VOICE=false ENABLE_WORKFLOWS=false \
  ENABLE_RAG=false ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false \
  ENABLE_HERMES=false ENABLE_OPENCLAW=false COMPOSE_FLAGS='' \
  bash -c '
    ods_progress() { :; }; show_phase() { :; }; ai() { :; }
    ai_ok() { :; }; ai_warn() { :; }; bootline() { :; }; signal() { :; }
    source "$SCRIPT_DIR/installers/phases/08-images.sh"
    printf "%s\n" "${PULL_LIST[@]}"
  '
} 2>/dev/null)"
if grep -qE 'LLAMA-SERVER|LEMONADE .*brain' <<<"$phase08_plan"; then
  echo "[FAIL] external Lemonade image plan still contains managed inference: $phase08_plan"
  exit 1
fi

echo "[contract] external Lemonade install verifies real completion"
grep -q '_phase12_verify_external_lemonade_completion' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 must verify a real external Lemonade completion"; exit 1; }
grep -q '/v1/chat/completions' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 completion check must call the LiteLLM chat route"; exit 1; }
grep -q '_phase12_model_looks_non_chat' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 must explain image/non-chat Lemonade model failures"; exit 1; }
grep -q 'bash install-core.sh --use-existing-lemonade' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 Lemonade recovery hint must work when install.sh is absent from the runtime tree"; exit 1; }
grep -q 'LEMONADE_MODEL=<chat-model-id>' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 Lemonade recovery hint must show inline LEMONADE_MODEL assignment"; exit 1; }

echo "[contract] external Lemonade completion reports HTTP failures honestly"
health_functions="$(awk '
  /^_phase12_env_get\(\)/ { emit=1 }
  /^_phase12_verify_external_llm_completion\(\)/ { emit=0 }
  emit { print }
' installers/phases/12-health.sh)"
[[ "$health_functions" == *'-w '\''%{http_code}'\'''* ]] \
  || { echo "[FAIL] phase 12 Lemonade completion must capture HTTP status"; exit 1; }
[[ "$health_functions" == *'External Lemonade completion route returned HTTP %s'* ]] \
  || { echo "[FAIL] phase 12 Lemonade completion must identify HTTP rejection"; exit 1; }
[[ "$health_functions" == *'"chat_template_kwargs":{"enable_thinking":false}'* ]] \
  || { echo "[FAIL] phase 12 Lemonade readiness must disable reasoning-token exhaustion"; exit 1; }

declare -A SERVICE_PORTS=([litellm]=4000)
INSTALL_DIR="${TMPDIR:-/tmp}/ods-lemonade-health-test"
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/ods-lemonade-health.XXXXXX")"
RED='' BGRN='' NC=''
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
health_output="$(_phase12_verify_external_lemonade_completion 2>&1)"
health_rc=$?
set -e
[[ "$health_rc" -ne 0 ]] \
  || { echo "[FAIL] phase 12 accepted a LiteLLM HTTP 503"; exit 1; }
[[ "$health_output" == *'returned HTTP 503'* ]] \
  || { echo "[FAIL] phase 12 did not surface the LiteLLM HTTP 503"; exit 1; }
[[ "$health_output" != *'returned no assistant content'* ]] \
  || { echo "[FAIL] phase 12 mislabeled an HTTP 503 as empty assistant content"; exit 1; }
grep -q 'No verified active model route is available yet' "$LOG_FILE" \
  || { echo "[FAIL] phase 12 did not retain the bounded HTTP error for diagnosis"; exit 1; }

STUB_CURL_STATUS=000
STUB_CURL_RC=28
STUB_CURL_BODY=''
set +e
transport_output="$(_phase12_verify_external_lemonade_completion 2>&1)"
transport_rc=$?
set -e
[[ "$transport_rc" -ne 0 ]] \
  || { echo "[FAIL] phase 12 accepted a failed Lemonade transport"; exit 1; }
[[ "$transport_output" == *'curl exit 28'* ]] \
  || { echo "[FAIL] phase 12 did not identify the curl transport failure"; exit 1; }

STUB_CURL_STATUS=200
STUB_CURL_RC=0
STUB_CURL_BODY='{"choices":[{"message":{"content":"OK"}}]}'
if ! success_output="$(_phase12_verify_external_lemonade_completion 2>&1)"; then
  echo "[FAIL] phase 12 rejected a valid external Lemonade completion: $success_output"
  exit 1
fi
[[ "$success_output" == *'completion route healthy'* ]] \
  || { echo "[FAIL] phase 12 did not report the valid completion as healthy"; exit 1; }

rm -f -- "$LOG_FILE"
unset -f curl

echo "[contract] external Lemonade preflight checks LiteLLM instead of managed llama-server"
grep -q 'is_external_lemonade()' ods-preflight.sh \
  || { echo "[FAIL] ods-preflight must detect external Lemonade mode"; exit 1; }
grep -q 'LiteLLM external Lemonade gateway' ods-preflight.sh \
  || { echo "[FAIL] ods-preflight must label the external Lemonade LiteLLM route"; exit 1; }
grep -q 'ods-litellm' ods-preflight.sh \
  || { echo "[FAIL] ods-preflight must check ods-litellm for external Lemonade"; exit 1; }

echo "[contract] doctor warns on unauthenticated host-routed external Lemonade"
grep -q 'ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE' scripts/ods-doctor.sh \
  || { echo "[FAIL] ods-doctor must warn when external Lemonade is host-routed without a user API key"; exit 1; }
grep -q 'sk-ods-lemonade-' scripts/ods-doctor.sh \
  || { echo "[FAIL] ods-doctor must distinguish installer-generated LiteLLM provider keys from user Lemonade API keys"; exit 1; }

echo "[contract] resolver selects cloud + external overlay instead of managed AMD overlay"
resolved="$(LEMONADE_EXTERNAL=true ODS_MODE=lemonade \
  ./scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --ods-mode lemonade --gpu-backend amd --tier SH_LARGE --env)"
grep -q 'docker-compose.cloud.yml' <<<"$resolved" \
  || { echo "[FAIL] external Lemonade must include cloud overlay to disable managed llama-server"; exit 1; }
grep -q 'docker-compose.lemonade-external.yml' <<<"$resolved" \
  || { echo "[FAIL] external Lemonade overlay missing from resolved stack"; exit 1; }
if grep -q 'docker-compose.amd.yml' <<<"$resolved"; then
  echo "[FAIL] external Lemonade must not include managed AMD overlay"
  exit 1
fi
if grep -q 'compose.local.yaml' <<<"$resolved"; then
  echo "[FAIL] external Lemonade must not include local llama-server dependency overlays"
  exit 1
fi
grep -Eq 'extensions[\\/]+services[\\/]+litellm[\\/]+compose.yaml' <<<"$resolved" \
  || { echo "[FAIL] external Lemonade must keep LiteLLM gateway enabled"; exit 1; }

echo "[contract] external Lemonade resolved compose config is valid"
echo "[contract] installer hardware profiles cannot override external Lemonade"
for backend in cpu amd nvidia; do
  for selector in explicit runtime; do
    installer_resolved="$(
      export SCRIPT_DIR="$ROOT_DIR" TIER=1 GPU_BACKEND="$backend" GPU_COUNT=1
      export ODS_MODE=lemonade
      export CAP_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.${backend}.yml"
      export LEMONADE_EXTERNAL=false AMD_INFERENCE_RUNTIME='' AMD_INFERENCE_MANAGED=''
      if [[ "$selector" == explicit ]]; then
        export LEMONADE_EXTERNAL=true
      else
        export AMD_INFERENCE_RUNTIME=lemonade AMD_INFERENCE_MANAGED=false
      fi
      LOG_FILE=/dev/null
      log() { :; }
      source installers/lib/compose-select.sh
      resolve_compose_config
      printf '%s\n' "$COMPOSE_FLAGS"
    )"
    [[ "$installer_resolved" == *docker-compose.cloud.yml* \
       && "$installer_resolved" == *docker-compose.lemonade-external.yml* \
       && "$installer_resolved" != *"docker-compose.${backend}.yml"* \
       && "$installer_resolved" != *compose.local.yaml* ]] \
      || { echo "[FAIL] $backend profile overrode $selector external Lemonade selection"; exit 1; }
  done
done

echo "[contract] local and managed Lemonade retain their hardware profiles"
for mode in local lemonade; do
  managed_resolved="$(LEMONADE_EXTERNAL=false AMD_INFERENCE_RUNTIME=lemonade \
    AMD_INFERENCE_MANAGED=true ODS_MODE="$mode" \
    ./scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" \
      --ods-mode "$mode" --gpu-backend amd --tier SH_LARGE \
      --profile-overlays docker-compose.base.yml,docker-compose.amd.yml --env)"
  [[ "$managed_resolved" == *docker-compose.amd.yml* \
     && "$managed_resolved" != *docker-compose.lemonade-external.yml* ]] \
    || { echo "[FAIL] $mode lost its managed hardware profile"; exit 1; }
done

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  compose_file_list="$(sed -n 's/^COMPOSE_FILE_LIST="\([^"]*\)".*/\1/p' <<<"$resolved")"
  compose_file_list="${compose_file_list//\\//}"
  IFS=',' read -r -a compose_files <<<"$compose_file_list"
  compose_args=()
  for compose_file in "${compose_files[@]}"; do
    [[ -n "$compose_file" ]] && compose_args+=(-f "$compose_file")
  done
  WEBUI_SECRET=test \
  HERMES_DASHBOARD_SESSION_TOKEN=test-hermes-dashboard-session-token \
  LITELLM_KEY=test \
  OPENCLAW_TOKEN=test \
  N8N_USER=test@example.local \
  N8N_PASS=test \
  SEARXNG_SECRET=test \
  ODS_SESSION_SECRET=test \
  LEMONADE_EXTERNAL=true \
  ODS_MODE=lemonade \
  GPU_BACKEND=amd \
  docker compose "${compose_args[@]}" config --services >/dev/null \
    || { echo "[FAIL] external Lemonade compose config must not have missing dependencies"; exit 1; }
else
  echo "[SKIP] docker compose unavailable; resolver assertions cover compose selection"
fi

echo "[contract] installer scopes firewall access for host Lemonade"
grep -q '_phase11_allow_external_lemonade_firewall' installers/phases/11-services.sh \
  || { echo "[FAIL] phase 11 must allow container-to-host external Lemonade access"; exit 1; }
grep -q 'ods-external-lemonade' installers/phases/11-services.sh \
  || { echo "[FAIL] external Lemonade firewall rule should be labeled"; exit 1; }

echo "[contract] CLI invalidates stale external Lemonade compose flags"
grep -q 'docker-compose.lemonade-external.yml' ods-cli \
  || { echo "[FAIL] ods-cli must recognize external Lemonade compose cache state"; exit 1; }
grep -q 'AMD_INFERENCE_MANAGED' ods-cli \
  || { echo "[FAIL] ods-cli must detect unmanaged external Lemonade installs"; exit 1; }
grep -q 'compose.local.yaml' ods-cli \
  || { echo "[FAIL] ods-cli must invalidate stale local dependency overlays"; exit 1; }

echo "[PASS] external Lemonade contracts"
