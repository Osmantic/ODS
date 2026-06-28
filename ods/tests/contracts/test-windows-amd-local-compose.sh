#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[contract] Windows AMD local compose overlay"

for f in \
  docker-compose.base.yml \
  installers/windows/docker-compose.windows-amd.yml \
  installers/windows/docker-compose.windows-amd.local.yml; do
  test -f "$f" || { echo "[FAIL] missing $f"; exit 1; }
done

grep -q 'ODS_TALK_VISION_URL=.*host.docker.internal' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD overlay must route ODS Talk vision calls to the host runtime"; exit 1; }
grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' installers/windows/docker-compose.windows-amd.yml \
  || { echo "[FAIL] Windows AMD overlay must give ODS Talk a long Hermes timeout for host inference"; exit 1; }
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
trap 'rm -f "$tmp_env"' EXIT
cat > "$tmp_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
OLLAMA_PORT=11434
LLM_API_BASE_PATH=/api/v1
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

echo "[PASS] Windows AMD local compose overlay"
