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

echo "[contract] installer scopes firewall access for host Lemonade"
grep -q '_phase11_allow_external_lemonade_firewall' installers/phases/11-services.sh \
  || { echo "[FAIL] phase 11 must allow container-to-host external Lemonade access"; exit 1; }
grep -q 'dream-external-lemonade' installers/phases/11-services.sh \
  || { echo "[FAIL] external Lemonade firewall rule should be labeled"; exit 1; }

echo "[PASS] external Lemonade contracts"
