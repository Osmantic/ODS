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
  extensions/services/litellm/compose.amd.yaml \
  extensions/services/openclaw/compose.yaml \
  extensions/services/openclaw/compose.amd.yaml; do
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
  || { echo "[FAIL] Windows env generation must provide the Docker Desktop host gateway used by OpenClaw"; exit 1; }
grep -q 'config.*litellm' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback must update LiteLLM local config"; exit 1; }
grep -q 'host.docker.internal:.*v1' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback LiteLLM config must route to the host /v1 endpoint"; exit 1; }
grep -q 'openai/\*' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback LiteLLM config must preserve wildcard routing"; exit 1; }
grep -q 'enable_thinking: false' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback LiteLLM config must disable Qwen thinking"; exit 1; }
grep -q 'request_timeout: 900' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback LiteLLM config must keep long-model request timeout at 900s"; exit 1; }
grep -q 'stream_timeout: 900' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows llama-server fallback LiteLLM config must keep long-model stream timeout at 900s"; exit 1; }

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "[SKIP] docker compose unavailable"
  exit 0
fi

tmp_env="$(mktemp)"
tmp_custom_port_env="$(mktemp)"
tmp_switchboard_env="$(mktemp)"
tmp_openclaw_windows_env="$(mktemp)"
tmp_openclaw_linux_env="$(mktemp)"
trap 'rm -f "$tmp_env" "$tmp_custom_port_env" "$tmp_switchboard_env" "$tmp_openclaw_windows_env" "$tmp_openclaw_linux_env"' EXIT
cat > "$tmp_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OLLAMA_PORT=11434
LLM_API_BASE_PATH=/api/v1
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
ENV_EOF

cat > "$tmp_openclaw_windows_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OPENCLAW_TOKEN=ci-openclaw-token
ODS_AGENT_HOST=host.docker.internal
AMD_INFERENCE_PORT=18080
SEARXNG_SECRET=ci-searxng-secret
N8N_USER=ci@example.test
N8N_PASS=ci-n8n-password
ENV_EOF

cat > "$tmp_openclaw_linux_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OPENCLAW_TOKEN=ci-openclaw-token
AMD_INFERENCE_PORT=8080
ENV_EOF

rendered="$(
  docker compose \
    --env-file "$tmp_env" \
    -f docker-compose.base.yml \
    -f installers/windows/docker-compose.windows-amd.yml \
    -f installers/windows/docker-compose.windows-amd.local.yml \
    config
)"

grep -q 'http://host.docker.internal:8080/api/v1/health' <<<"$rendered" \
  || { echo "[FAIL] Lemonade readiness probe must use native Windows port 8080"; exit 1; }
grep -q 'http://host.docker.internal:8080/health' <<<"$rendered" \
  || { echo "[FAIL] llama-server readiness probe must use native Windows port 8080"; exit 1; }
grep -q 'ODS_TALK_VISION_URL: http://host.docker.internal:8080/api/v1' <<<"$rendered" \
  || { echo "[FAIL] ODS Talk vision URL must use the Windows AMD host runtime API path"; exit 1; }
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

grep -q 'http://host.docker.internal:18080/api/v1/health' <<<"$custom_port_rendered" \
  || { echo "[FAIL] Lemonade readiness probe must honor AMD_INFERENCE_PORT"; exit 1; }
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

grep -qF '"http://host.docker.internal:$($script:LEMONADE_PORT)/api"' installers/windows/phases/06-directories.ps1 \
  || { echo "[FAIL] Windows OpenClaw config must honor the resolved native port"; exit 1; }
grep -qF '"--port", [string]$script:LEMONADE_PORT' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows installer must launch native llama-server on the resolved port"; exit 1; }
grep -qF '$script:LEMONADE_PORT = if ($cloudMode)' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] AMD cloud installs must retain a valid native-port default"; exit 1; }
grep -qF '"http://host.docker.internal:$($llmEndpoint['"'"'Port'"'"'])/v1"' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows Perplexica config must honor the resolved native port"; exit 1; }

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
    -f extensions/services/litellm/compose.yaml \
    -f extensions/services/litellm/compose.amd.yaml \
    config litellm
)"

grep -q 'CONFIG_PATH=/app/config.yaml' <<<"$switchboard_litellm_rendered" \
  || { echo "[FAIL] AMD LiteLLM render must keep the base CONFIG_PATH command"; exit 1; }
grep -q 'CONFIG_PATH=/app/switchboard.yaml' <<<"$switchboard_litellm_rendered" \
  || { echo "[FAIL] AMD LiteLLM render must keep switchboard.yaml selection"; exit 1; }

# Match the Windows installer's precedence: platform overlays are loaded before
# extension base/GPU overlays. Rendering the complete stack catches a later
# compose.amd.yaml accidentally restoring the disabled llama-server endpoint.
openclaw_windows_compose_args=(
  --env-file "$tmp_openclaw_windows_env"
  -f docker-compose.base.yml
  -f installers/windows/docker-compose.windows-amd.yml
  -f installers/windows/docker-compose.windows-amd.local.yml
)
for extension_dir in extensions/services/*/; do
  [[ -f "${extension_dir}compose.yaml" ]] \
    && openclaw_windows_compose_args+=(-f "${extension_dir}compose.yaml")
  [[ -f "${extension_dir}compose.amd.yaml" ]] \
    && openclaw_windows_compose_args+=(-f "${extension_dir}compose.amd.yaml")
done

openclaw_windows_rendered="$(
  env -u ODS_AGENT_HOST -u AMD_INFERENCE_PORT \
    docker compose "${openclaw_windows_compose_args[@]}" config openclaw
)"

openclaw_windows_ollama_url="$(sed -n 's/^[[:space:]]*OLLAMA_URL:[[:space:]]*//p' <<<"$openclaw_windows_rendered")"
if [[ "$openclaw_windows_ollama_url" != "http://host.docker.internal:18080/api" ]]; then
  echo "[FAIL] Windows AMD OpenClaw OLLAMA_URL mismatch: ${openclaw_windows_ollama_url:-<missing>}"
  exit 1
fi

openclaw_linux_rendered="$(
  env -u ODS_AGENT_HOST -u AMD_INFERENCE_PORT \
    docker compose \
    --env-file "$tmp_openclaw_linux_env" \
    -f docker-compose.base.yml \
    -f docker-compose.amd.yml \
    -f extensions/services/openclaw/compose.yaml \
    -f extensions/services/openclaw/compose.amd.yaml \
    config openclaw
)"

openclaw_linux_ollama_url="$(sed -n 's/^[[:space:]]*OLLAMA_URL:[[:space:]]*//p' <<<"$openclaw_linux_rendered")"
if [[ "$openclaw_linux_ollama_url" != "http://llama-server:8080/api" ]]; then
  echo "[FAIL] Linux AMD OpenClaw OLLAMA_URL mismatch: ${openclaw_linux_ollama_url:-<missing>}"
  exit 1
fi

echo "[PASS] Windows AMD local compose overlay"
