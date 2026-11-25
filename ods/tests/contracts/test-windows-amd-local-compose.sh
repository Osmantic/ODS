#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[contract] Windows AMD local compose overlay"

for f in \
  docker-compose.base.yml \
  docker-compose.amd.yml \
  installers/windows/docker-compose.windows-amd.yml \
  installers/windows/docker-compose.windows-amd.local.yml \
  extensions/services/litellm/compose.yaml \
  extensions/services/litellm/compose.amd.yaml; do
  test -f "$f" || { echo "[FAIL] missing $f"; exit 1; }
done

grep -q 'ODS_TALK_VISION_URL=.*host.docker.internal' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD overlay must route ODS Talk vision calls to the host runtime"; exit 1; }
grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD overlay must give ODS Talk a long Hermes timeout for host inference"; exit 1; }
grep -q 'OPEN_WEBUI_LLM_BASE_URL' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD Open WebUI overlay must respect switchboard gateway env"; exit 1; }
if grep -q 'exec litellm --config /app/config.yaml' extensions/services/litellm/compose.amd.yaml; then
  echo "[FAIL] AMD LiteLLM overlay must not bypass the base switchboard-aware command"
  exit 1
fi
grep -qF 'ODS_AGENT_HOST=$(Get-EnvOrNew "ODS_AGENT_HOST" "host.docker.internal")' installers/windows/lib/env-generator.ps1 \
  || { echo "[FAIL] Windows env generation must provide the Docker Desktop host gateway for host services"; exit 1; }
# Round F: phase 06 renders the native llama-server's LiteLLM config
# directly (env-generator.ps1); the installer no longer patches it afterwards.
win_env_generator=installers/windows/lib/env-generator.ps1
grep -qF '# ODS-CONTRACT-WRITER: litellm-local-native' "$win_env_generator" \
  || { echo "[FAIL] Windows env generation must own the native LiteLLM local config"; exit 1; }
if grep -q 'ODS-CONTRACT-WRITER: litellm-local-native' installers/windows/install-windows.ps1; then
  echo "[FAIL] Windows installer must not patch LiteLLM after the native start (phase 06 renders it)"
  exit 1
fi
grep -qF '"http://host.docker.internal:$nativeInferencePort"' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must route to the host llama-server port"; exit 1; }
grep -qF '$llmApiBasePath = "/v1"' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must use the host /v1 endpoint"; exit 1; }
grep -qF 'os.environ/LLAMA_SERVER_API_KEY' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must send LLAMA_SERVER_API_KEY from the environment"; exit 1; }
# LiteLLM may be off on a Windows install, so the key cannot come from an
# overlay stanza (Compose rejects a service with no image); LiteLLM's own
# fragment passes it whenever LiteLLM runs.
awk '$0=="    environment:" {on=1; next} /^    [a-z]/ {on=0} on' extensions/services/litellm/compose.yaml \
  | grep -qF -- '- LLAMA_SERVER_API_KEY=${LLAMA_SERVER_API_KEY:-}' \
  || { echo "[FAIL] LiteLLM must receive LLAMA_SERVER_API_KEY for the Windows native config that names it"; exit 1; }
grep -q 'model_name: "\*"' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must preserve wildcard routing"; exit 1; }
grep -q 'enable_thinking: false' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must disable Qwen thinking"; exit 1; }
grep -q 'request_timeout: 900' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must keep long-model request timeout at 900s"; exit 1; }
grep -q 'stream_timeout: 900' "$win_env_generator" \
  || { echo "[FAIL] Windows native LiteLLM config must keep long-model stream timeout at 900s"; exit 1; }
if grep -q '/api/v1' installers/windows/docker-compose.windows-amd.yml installers/windows/docker-compose.windows-amd.local.yml; then
  echo "[FAIL] Windows AMD overlays must not probe or route to Lemonade's /api/v1"
  exit 1
fi
for service in dashboard-api model-router; do
  awk -v svc="  ${service}:" '$0==svc {on=1; next} /^  [a-z]/ {on=0} on' installers/windows/docker-compose.windows-amd.yml \
    | grep -qF 'LLAMA_SERVER_API_KEY=${LLAMA_SERVER_API_KEY:-}' \
    || { echo "[FAIL] Windows AMD overlay must give ${service} the native llama-server key"; exit 1; }
done
grep -qF 'OPENAI_API_KEY: "${OPEN_WEBUI_LLM_API_KEY:-${LLAMA_SERVER_API_KEY:-' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Open WebUI must authenticate to the native llama-server when it calls it directly"; exit 1; }
grep -qF 'LLM_BACKEND=${LLM_BACKEND:-llama-server}' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD overlay must default LLM_BACKEND to llama-server"; exit 1; }

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "[SKIP] docker compose unavailable"
  exit 0
fi

tmp_env="$(mktemp)"
tmp_custom_port_env="$(mktemp)"
tmp_switchboard_env="$(mktemp)"
tmp_full_stack_env="$(mktemp)"
trap 'rm -f "$tmp_env" "$tmp_custom_port_env" "$tmp_switchboard_env" "$tmp_full_stack_env"' EXIT
cat > "$tmp_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OLLAMA_PORT=11434
LLM_API_BASE_PATH=/v1
LLAMA_SERVER_API_KEY=ci-native-key
ENV_EOF

cat > "$tmp_custom_port_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
AMD_INFERENCE_PORT=18080
ENV_EOF

cat > "$tmp_switchboard_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OLLAMA_PORT=11434
LLM_API_BASE_PATH=/api/v1
ODS_MODEL_SWITCHBOARD=enabled
LITELLM_KEY=ci-litellm-key
OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000
OPEN_WEBUI_LLM_API_KEY=ci-litellm-key
LLAMA_SERVER_API_KEY=ci-native-key
ENV_EOF

cat > "$tmp_full_stack_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
HERMES_DASHBOARD_SESSION_TOKEN=ci-hermes-dashboard-session-token
ODS_AGENT_HOST=host.docker.internal
AMD_INFERENCE_PORT=18080
SEARXNG_SECRET=ci-searxng-secret
N8N_USER=ci@example.test
N8N_PASS=ci-n8n-password
ENV_EOF

rendered="$(
  docker compose \
    --env-file "$tmp_env" \
    -f docker-compose.base.yml \
    -f installers/windows/docker-compose.windows-amd.yml \
    -f installers/windows/docker-compose.windows-amd.local.yml \
    config
)"

grep -q 'http://host.docker.internal:8080/health' <<<"$rendered" \
  || { echo "[FAIL] llama-server readiness probe must use native Windows port 8080"; exit 1; }
if grep -q '/api/v1' <<<"$rendered"; then
  echo "[FAIL] Windows AMD render still references Lemonade's /api/v1"
  exit 1
fi
grep -q 'ODS_TALK_VISION_URL: http://host.docker.internal:8080/v1' <<<"$rendered" \
  || { echo "[FAIL] ODS Talk vision URL must use the Windows AMD host runtime API path"; exit 1; }
grep -q 'OPENAI_API_KEY: ci-native-key' <<<"$rendered" \
  || { echo "[FAIL] Open WebUI must send the native llama-server key"; exit 1; }
grep -q 'LLAMA_SERVER_API_KEY: ci-native-key' <<<"$rendered" \
  || { echo "[FAIL] dashboard-api and model-router must receive the native llama-server key"; exit 1; }
grep -q 'ODS_TALK_HERMES_TIMEOUT: "900"' <<<"$rendered" \
  || { echo "[FAIL] Windows AMD ODS Talk Hermes timeout must render as 900s"; exit 1; }
if grep -q 'host.docker.internal:11434' <<<"$rendered"; then
  echo "[FAIL] Windows AMD local overlay must not inherit OLLAMA_PORT=11434"
  exit 1
fi
grep -q 'condition: service_healthy' <<<"$rendered" \
  || { echo "[FAIL] open-webui must wait for llama-server-ready health"; exit 1; }

custom_port_rendered="$(
  docker compose \
    --env-file "$tmp_custom_port_env" \
    -f docker-compose.base.yml \
    -f installers/windows/docker-compose.windows-amd.yml \
    -f installers/windows/docker-compose.windows-amd.local.yml \
    config
)"

grep -q 'http://host.docker.internal:18080/health' <<<"$custom_port_rendered" \
  || { echo "[FAIL] llama-server readiness probe must honor AMD_INFERENCE_PORT"; exit 1; }
if grep -q 'host.docker.internal:8080' <<<"$custom_port_rendered"; then
  echo "[FAIL] custom Windows native port render retained hard-coded 8080"
  exit 1
fi
grep -q 'OLLAMA_URL: http://host.docker.internal:18080' <<<"$custom_port_rendered" \
  || { echo "[FAIL] dashboard-api must honor AMD_INFERENCE_PORT"; exit 1; }
grep -q 'OPENAI_API_BASE_URL: http://host.docker.internal:18080/v1' <<<"$custom_port_rendered" \
  || { echo "[FAIL] Open WebUI must honor AMD_INFERENCE_PORT"; exit 1; }

grep -qF "'--port', [string]\$Plan.Port" installers/windows/lib/native-llama-runtime.ps1 \
  || { echo "[FAIL] Windows native llama-server must launch on its planned port"; exit 1; }
grep -qF -- '-Port $script:NATIVE_LLM_PORT' installers/windows/ods.ps1 \
  || { echo "[FAIL] ods.ps1 must launch native llama-server on the resolved port"; exit 1; }
grep -qF '$script:NATIVE_LLM_PORT = Resolve-WindowsLlmPreflightPort' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows installer must resolve the persisted native port before rendering .env"; exit 1; }
grep -qF '$perplexicaUsesLiteLlm = ($cloudMode -or [string]$llmEndpoint["Backend"] -eq "native-llama-server")' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows Perplexica must reach the keyed native llama-server through LiteLLM"; exit 1; }

switchboard_webui_rendered="$(
  docker compose \
    --env-file "$tmp_switchboard_env" \
    -f docker-compose.base.yml \
    -f installers/windows/docker-compose.windows-amd.yml \
    -f installers/windows/docker-compose.windows-amd.local.yml \
    config open-webui
)"

grep -q 'OPENAI_API_BASE_URL: http://litellm:4000' <<<"$switchboard_webui_rendered" \
  || { echo "[FAIL] Windows AMD switchboard mode must route Open WebUI through LiteLLM"; exit 1; }
grep -q 'OPENAI_API_KEY: ci-litellm-key' <<<"$switchboard_webui_rendered" \
  || { echo "[FAIL] Windows AMD switchboard mode must pass the LiteLLM key to Open WebUI"; exit 1; }

switchboard_litellm_rendered="$(
  docker compose \
    --env-file "$tmp_switchboard_env" \
    -f docker-compose.base.yml \
    -f extensions/services/litellm/compose.yaml \
    -f extensions/services/litellm/compose.amd.yaml \
    config litellm
)"

grep -q 'ODS_MODE: local' <<<"$switchboard_litellm_rendered" \
  || { echo "[FAIL] AMD LiteLLM render must receive the active ODS mode"; exit 1; }
grep -q 'ods-select-config.sh' <<<"$switchboard_litellm_rendered" \
  || { echo "[FAIL] AMD LiteLLM render must keep the mode-aware config selector"; exit 1; }

# Match the Windows installer's precedence: platform overlays are loaded before
# extension base/GPU overlays. Rendering the complete stack catches a later
# extension compose.yaml or compose.amd.yaml that points a core service back at
# the disabled in-network llama-server.
full_stack_compose_args=(
  --env-file "$tmp_full_stack_env"
  -f docker-compose.base.yml
  -f installers/windows/docker-compose.windows-amd.yml
  -f installers/windows/docker-compose.windows-amd.local.yml
)
for extension_dir in extensions/services/*/; do
  [[ -f "${extension_dir}compose.yaml" ]] \
    && full_stack_compose_args+=(-f "${extension_dir}compose.yaml")
  [[ -f "${extension_dir}compose.amd.yaml" ]] \
    && full_stack_compose_args+=(-f "${extension_dir}compose.amd.yaml")
done

full_stack_webui_rendered="$(
  env -u ODS_AGENT_HOST -u AMD_INFERENCE_PORT -u OPEN_WEBUI_LLM_BASE_URL -u LLM_API_BASE_PATH \
    docker compose "${full_stack_compose_args[@]}" config open-webui
)"
full_stack_webui_url="$(sed -n 's/^[[:space:]]*OPENAI_API_BASE_URL:[[:space:]]*//p' <<<"$full_stack_webui_rendered")"
if [[ "$full_stack_webui_url" != "http://host.docker.internal:18080/v1" ]]; then
  echo "[FAIL] Windows AMD full-stack Open WebUI OPENAI_API_BASE_URL mismatch: ${full_stack_webui_url:-<missing>}"
  exit 1
fi

full_stack_dashboard_rendered="$(
  env -u ODS_AGENT_HOST -u AMD_INFERENCE_PORT -u LLM_API_BASE_PATH \
    docker compose "${full_stack_compose_args[@]}" config dashboard-api
)"
full_stack_dashboard_url="$(sed -n 's/^[[:space:]]*OLLAMA_URL:[[:space:]]*//p' <<<"$full_stack_dashboard_rendered")"
if [[ "$full_stack_dashboard_url" != "http://host.docker.internal:18080" ]]; then
  echo "[FAIL] Windows AMD full-stack dashboard-api OLLAMA_URL mismatch: ${full_stack_dashboard_url:-<missing>}"
  exit 1
fi

echo "[PASS] Windows AMD local compose overlay"
