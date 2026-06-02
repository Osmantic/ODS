#!/usr/bin/env bash
# External Lemonade SDK runtime contract tests.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
PYTHON_CMD="${DREAM_PYTHON_CMD:-python3}"

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
  --dream-mode lemonade \
  --gpu-backend amd \
  --lemonade-model-id Qwen3-0.6B-GGUF \
  --lemonade-api-base http://host.docker.internal:13305/api/v1)"
grep -q 'openai/Qwen3-0.6B-GGUF' <<<"$rendered" \
  || { echo "[FAIL] renderer must use supplied Lemonade model id"; exit 1; }
grep -q 'host.docker.internal:13305/api/v1' <<<"$rendered" \
  || { echo "[FAIL] renderer must use supplied Lemonade API base"; exit 1; }

echo "[contract] installer discovers external Lemonade model and avoids stale fallbacks"
grep -q '_phase06_discover_lemonade_model' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must discover the model served by external Lemonade"; exit 1; }
if grep -q 'LLM_MODEL_VALUE' installers/phases/06-directories.sh; then
  echo "[FAIL] phase 06 must not reference undefined LLM_MODEL_VALUE"
  exit 1
fi
grep -q 'LEMONADE_MODEL_VALUE' installers/phases/06-directories.sh \
  || { echo "[FAIL] phase 06 must write a resolved LEMONADE_MODEL value"; exit 1; }

echo "[contract] external Lemonade does not pull managed Lemonade image"
grep -q '_lemonade_external' installers/phases/08-images.sh \
  || { echo "[FAIL] phase 08 must skip managed Lemonade image pulls in external mode"; exit 1; }

echo "[contract] external Lemonade install verifies real completion"
grep -q '_phase12_verify_external_lemonade_completion' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 must verify a real external Lemonade completion"; exit 1; }
grep -q '/v1/chat/completions' installers/phases/12-health.sh \
  || { echo "[FAIL] phase 12 completion check must call the LiteLLM chat route"; exit 1; }

echo "[contract] external Lemonade preflight checks LiteLLM instead of managed llama-server"
grep -q 'is_external_lemonade()' dream-preflight.sh \
  || { echo "[FAIL] dream-preflight must detect external Lemonade mode"; exit 1; }
grep -q 'LiteLLM external Lemonade gateway' dream-preflight.sh \
  || { echo "[FAIL] dream-preflight must label the external Lemonade LiteLLM route"; exit 1; }
grep -q 'dream-litellm' dream-preflight.sh \
  || { echo "[FAIL] dream-preflight must check dream-litellm for external Lemonade"; exit 1; }

echo "[contract] resolver selects cloud + external overlay instead of managed AMD overlay"
resolved="$(LEMONADE_EXTERNAL=true DREAM_MODE=lemonade \
  ./scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --dream-mode lemonade --gpu-backend amd --tier SH_LARGE --env)"
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
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  compose_file_list="$(sed -n 's/^COMPOSE_FILE_LIST="\([^"]*\)".*/\1/p' <<<"$resolved")"
  compose_file_list="${compose_file_list//\\//}"
  IFS=',' read -r -a compose_files <<<"$compose_file_list"
  compose_args=()
  for compose_file in "${compose_files[@]}"; do
    [[ -n "$compose_file" ]] && compose_args+=(-f "$compose_file")
  done
  WEBUI_SECRET=test \
  LITELLM_KEY=test \
  OPENCLAW_TOKEN=test \
  N8N_USER=test@example.local \
  N8N_PASS=test \
  SEARXNG_SECRET=test \
  DREAM_SESSION_SECRET=test \
  LEMONADE_EXTERNAL=true \
  DREAM_MODE=lemonade \
  GPU_BACKEND=amd \
  docker compose "${compose_args[@]}" config --services >/dev/null \
    || { echo "[FAIL] external Lemonade compose config must not have missing dependencies"; exit 1; }
else
  echo "[SKIP] docker compose unavailable; resolver assertions cover compose selection"
fi

echo "[contract] installer scopes firewall access for host Lemonade"
grep -q '_phase11_allow_external_lemonade_firewall' installers/phases/11-services.sh \
  || { echo "[FAIL] phase 11 must allow container-to-host external Lemonade access"; exit 1; }
grep -q 'dream-external-lemonade' installers/phases/11-services.sh \
  || { echo "[FAIL] external Lemonade firewall rule should be labeled"; exit 1; }

echo "[contract] CLI invalidates stale external Lemonade compose flags"
grep -q 'docker-compose.lemonade-external.yml' dream-cli \
  || { echo "[FAIL] dream-cli must recognize external Lemonade compose cache state"; exit 1; }
grep -q 'AMD_INFERENCE_MANAGED' dream-cli \
  || { echo "[FAIL] dream-cli must detect unmanaged external Lemonade installs"; exit 1; }
grep -q 'compose.local.yaml' dream-cli \
  || { echo "[FAIL] dream-cli must invalidate stale local dependency overlays"; exit 1; }

echo "[PASS] external Lemonade contracts"
