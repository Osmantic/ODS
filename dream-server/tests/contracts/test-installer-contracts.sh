#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

command -v jq >/dev/null 2>&1 || {
  echo "[FAIL] jq is required"
  exit 1
}

echo "[contract] backend contract files"
for f in config/backends/amd.json config/backends/nvidia.json config/backends/cpu.json config/backends/apple.json; do
  test -f "$f" || { echo "[FAIL] missing $f"; exit 1; }
  jq -e '.id and .llm_engine and .service_name and .public_api_port and .public_health_url and .provider_name and .provider_url' "$f" >/dev/null \
    || { echo "[FAIL] invalid backend contract: $f"; exit 1; }
done

echo "[contract] hardware class mapping"
test -f config/hardware-classes.json || { echo "[FAIL] missing config/hardware-classes.json"; exit 1; }
jq -e '.version and (.classes | type=="array" and length>0)' config/hardware-classes.json >/dev/null \
  || { echo "[FAIL] invalid hardware-classes root structure"; exit 1; }

for class_id in strix_unified nvidia_pro apple_silicon cpu_fallback; do
  jq -e --arg id "$class_id" '.classes[] | select(.id==$id) | .recommended.backend and .recommended.tier and .recommended.compose_overlays' config/hardware-classes.json >/dev/null \
    || { echo "[FAIL] missing/invalid class: $class_id"; exit 1; }
done

echo "[contract] capability profile schema has hardware_class"
jq -e '.properties.hardware_class and (.required | index("hardware_class"))' config/capability-profile.schema.json >/dev/null \
  || { echo "[FAIL] capability profile schema missing hardware_class"; exit 1; }

echo "[contract] AMD phase-06 env keys exist in schema"
for key in HSA_XNACK AMDGPU_TARGET LLAMA_CPP_REF; do
  jq -e --arg key "$key" '.properties[$key]' .env.schema.json >/dev/null \
    || { echo "[FAIL] .env.schema.json missing AMD installer key: $key"; exit 1; }
done

echo "[contract] canonical port contract parity"
test -x tests/contracts/test-port-contracts.sh || { echo "[FAIL] script not executable: tests/contracts/test-port-contracts.sh"; exit 1; }
bash tests/contracts/test-port-contracts.sh

echo "[contract] Windows AMD local compose readiness"
bash tests/contracts/test-windows-amd-local-compose.sh

echo "[contract] dashboard diagnostics route through docker network URLs"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  tmp_env="$(mktemp)"
  trap 'rm -f "$tmp_env"' EXIT
  cat > "$tmp_env" <<'ENV_EOF'
WEBUI_SECRET=ci-placeholder
LLM_API_URL=http://litellm:4000
ENV_EOF
  rendered="$(docker compose --env-file "$tmp_env" -f docker-compose.base.yml config dashboard-api)"
  grep -q 'LLM_URL: http://litellm:4000' <<<"$rendered" \
    || { echo "[FAIL] dashboard-api diagnostics LLM_URL must follow LLM_API_URL when LLM_URL is unset"; exit 1; }
  grep -q 'OLLAMA_URL: http://litellm:4000' <<<"$rendered" \
    || { echo "[FAIL] dashboard-api OLLAMA_URL lost LLM_API_URL routing"; exit 1; }
  grep -q 'TTS_URL: http://tts:8880' <<<"$rendered" \
    || { echo "[FAIL] dashboard-api diagnostics TTS_URL must use docker network hostname"; exit 1; }
  grep -q 'EMBEDDING_URL: http://embeddings:80' <<<"$rendered" \
    || { echo "[FAIL] dashboard-api diagnostics EMBEDDING_URL must use docker network hostname"; exit 1; }
  grep -q 'WHISPER_URL: http://whisper:8000' <<<"$rendered" \
    || { echo "[FAIL] dashboard-api diagnostics WHISPER_URL must use docker network hostname"; exit 1; }
else
  echo "[SKIP] docker compose unavailable"
fi

echo "[contract] resolver scripts executable"
for s in scripts/build-capability-profile.sh scripts/classify-hardware.sh scripts/load-backend-contract.sh scripts/resolve-compose-stack.sh scripts/preflight-engine.sh scripts/dream-doctor.sh scripts/simulate-installers.sh; do
  test -x "$s" || { echo "[FAIL] script not executable: $s"; exit 1; }
done

echo "[contract] Langfuse telemetry suppression"
grep -q 'TELEMETRY_ENABLED.*false' extensions/services/langfuse/compose.yaml.disabled 2>/dev/null || \
  grep -q 'TELEMETRY_ENABLED.*false' extensions/services/langfuse/compose.yaml 2>/dev/null || \
  { echo "[FAIL] Langfuse app telemetry not disabled"; exit 1; }

grep -q 'NEXT_TELEMETRY_DISABLED.*1' extensions/services/langfuse/compose.yaml.disabled 2>/dev/null || \
  grep -q 'NEXT_TELEMETRY_DISABLED.*1' extensions/services/langfuse/compose.yaml 2>/dev/null || \
  { echo "[FAIL] Next.js telemetry not disabled"; exit 1; }

grep -q 'MINIO_TELEMETRY_DISABLED.*1' extensions/services/langfuse/compose.yaml.disabled 2>/dev/null || \
  grep -q 'MINIO_TELEMETRY_DISABLED.*1' extensions/services/langfuse/compose.yaml 2>/dev/null || \
  { echo "[FAIL] MinIO telemetry not disabled"; exit 1; }

echo "[contract] ENABLE_RAG opt-out disables both qdrant and embeddings"
# RAG = qdrant (vector store) + embeddings (TEI). Both compose files must
# be gated on ENABLE_RAG in installers/phases/03-features.sh; otherwise
# answering 'n' to the Custom-menu RAG prompt still leaves embeddings
# being pulled and started.
features_phase="dream-server/installers/phases/03-features.sh"
test -f "$features_phase" || features_phase="installers/phases/03-features.sh"
test -f "$features_phase" || { echo "[FAIL] cannot locate 03-features.sh"; exit 1; }
for svc in qdrant embeddings; do
  grep -qE "_sync_extension_compose +\"\\\$\\{ENABLE_RAG:-\\}\" +$svc\\b" "$features_phase" \
    || { echo "[FAIL] ENABLE_RAG opt-out missing sync for '$svc' in $features_phase"; exit 1; }
done

echo "[contract] Token Spy dashboard ships offline chart assets"
test -f extensions/services/token-spy/dashboard_charts.js || { echo "[FAIL] missing extensions/services/token-spy/dashboard_charts.js"; exit 1; }
grep -q '/dashboard-assets/charts.js' extensions/services/token-spy/main.py || \
  { echo "[FAIL] Token Spy dashboard missing local chart asset reference"; exit 1; }
if grep -q 'cdn.jsdelivr.net/npm/chart.js\|cdn.jsdelivr.net/npm/chartjs-adapter-date-fns' extensions/services/token-spy/main.py; then
  echo "[FAIL] Token Spy dashboard still depends on CDN chart assets"
  exit 1
fi

echo "[contract] installers pre-mark setup wizard complete"
# All three installers must write data/config/setup-complete.json at install time
# so the dashboard wizard doesn't reappear on every visit after a fresh install.
# dashboard-api reads this file (container path /data/config/setup-complete.json,
# mounted from ${INSTALL_DIR}/data) to decide first_run state.
grep -q 'data/config/setup-complete.json' installers/phases/13-summary.sh \
  || { echo "[FAIL] Linux phase 13 does not write data/config/setup-complete.json"; exit 1; }
grep -q 'data/config/setup-complete.json' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not write data/config/setup-complete.json"; exit 1; }
grep -q 'data\\\\config\\\\setup-complete.json\|setup-complete.json' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows installer does not write setup-complete.json"; exit 1; }

echo "[contract] macOS compose resolver installs PyYAML into checked python3"
grep -q "python3 -c 'import yaml'" installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not verify PyYAML with python3"; exit 1; }
grep -q 'python3 -m pip install --user .*pyyaml' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must install PyYAML via python3 -m pip, not a possibly unrelated pip3"; exit 1; }

echo "[contract] Hermes context defaults are installer-wide"
grep -q '^BOOTSTRAP_MAX_CONTEXT=65536$' installers/lib/bootstrap-model.sh \
  || { echo "[FAIL] Linux bootstrap context must meet Hermes 64K floor"; exit 1; }
grep -q '^BOOTSTRAP_MAX_CONTEXT=65536$' installers/macos/lib/tier-map.sh \
  || { echo "[FAIL] macOS bootstrap context must meet Hermes 64K floor"; exit 1; }
grep -q 'BOOTSTRAP_MAX_CONTEXT.*65536' installers/windows/lib/tier-map.ps1 \
  || { echo "[FAIL] Windows bootstrap context must meet Hermes 64K floor"; exit 1; }
grep -q '"CTX_SIZE=$MAX_CONTEXT"' installers/phases/11-services.sh \
  || { echo "[FAIL] Linux bootstrap .env patch must update CTX_SIZE as well as MAX_CONTEXT"; exit 1; }
grep -q 'Patched Hermes config for bootstrap model' installers/windows/install-windows.ps1 \
  || { echo "[FAIL] Windows bootstrap path must re-patch Hermes config after switching to bootstrap model"; exit 1; }
grep -q 'threshold: 0.50' extensions/services/hermes/cli-config.yaml.template \
  || { echo "[FAIL] Hermes compression threshold must use upstream fractional semantics"; exit 1; }

python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
test -n "$python_cmd" || { echo "[FAIL] python is required to test Hermes config patcher"; exit 1; }
tmp_hermes="$(mktemp)"
cat > "$tmp_hermes" <<'HERMES_EOF'
model:
  default: "old-model"
  provider: "custom"
  base_url: "http://old.example/v1"
  context_length: 8192

compression:
  threshold: 10000
  target_ratio: 0.5
terminal:
  backend: "local"
HERMES_EOF
"$python_cmd" scripts/patch-hermes-config.py "$tmp_hermes" \
  --model "Qwen3.5-2B-Q4_K_M.gguf" \
  --base-url "http://llama-server:8080/v1" \
  --context-length 65536 >/dev/null
grep -q 'default: "Qwen3.5-2B-Q4_K_M.gguf"' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not update model.default"; rm -f "$tmp_hermes"; exit 1; }
grep -q 'base_url: "http://llama-server:8080/v1"' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not update base_url"; rm -f "$tmp_hermes"; exit 1; }
grep -q '^  context_length: 65536$' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not update model.context_length"; rm -f "$tmp_hermes"; exit 1; }
grep -q '^    context_length: 65536$' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not add auxiliary compression context"; rm -f "$tmp_hermes"; exit 1; }
grep -q '^  threshold: 0.50$' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not normalize compression threshold"; rm -f "$tmp_hermes"; exit 1; }
grep -q '^  target_ratio: 0.20$' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not normalize compression target_ratio"; rm -f "$tmp_hermes"; exit 1; }
grep -q '^  protect_last_n: 20$' "$tmp_hermes" \
  || { echo "[FAIL] Hermes patcher did not add protect_last_n"; rm -f "$tmp_hermes"; exit 1; }
rm -f "$tmp_hermes"

echo "[PASS] installer contracts"
