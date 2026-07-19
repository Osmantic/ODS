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

echo "[contract] external Lemonade compose overlay readiness"
bash tests/contracts/test-external-lemonade-contracts.sh

echo "[contract] bootstrap hot-swap force-recreate"
bash tests/test-bootstrap-upgrade-hotswap-contract.sh
bash tests/contracts/test-windows-lemonade-swap-wait.sh

echo "[contract] bootstrap Docker hot-swap rollback"
bash tests/test-bootstrap-upgrade-docker-rollback.sh

echo "[contract] ODS rename migration guardrails"
grep -qF 'ODS_ALLOW_LEGACY_PARALLEL' get-ods.sh \
  || { echo "[FAIL] get-ods.sh must require an explicit override before parallel pre-ODS installs"; exit 1; }
grep -qF 'ODS_LEGACY_INSTALL_DIR' get-ods.sh \
  || { echo "[FAIL] get-ods.sh must allow an explicit pre-ODS install path"; exit 1; }
grep -qF 'PRE_ODS_INSTALL_DIR="${ODS_LEGACY_INSTALL_DIR:-}"' get-ods.sh \
  || { echo "[FAIL] get-ods.sh must use only the canonical pre-ODS install-dir control"; exit 1; }
grep -qF 'ODS_INSTALL_DIR' get-ods.sh \
  || { echo "[FAIL] get-ods.sh must allow an explicit ODS install dir for isolated parallel testing"; exit 1; }
grep -qF 'ODS_ALLOW_LEGACY_PARALLEL' installers/phases/01-preflight.sh \
  || { echo "[FAIL] Linux installer preflight must gate pre-ODS coexistence"; exit 1; }
grep -qF '_pre_ods_install_dir="${ODS_LEGACY_INSTALL_DIR:-}"' installers/phases/01-preflight.sh \
  || { echo "[FAIL] Linux installer preflight must use only the canonical pre-ODS install-dir control"; exit 1; }
grep -qF '_ods_is_related_install_dir' installers/phases/01-preflight.sh \
  || { echo "[FAIL] Linux installer preflight must auto-detect dormant related installs"; exit 1; }
grep -qF '_ods_related_compose_containers' installers/phases/01-preflight.sh \
  || { echo "[FAIL] Linux installer preflight must auto-detect related Compose projects"; exit 1; }
for _pre_ods_scan_file in get-ods.sh installers/phases/01-preflight.sh; do
  grep -qF '\( -type d -o -type l \)' "$_pre_ods_scan_file" \
    || { echo "[FAIL] $_pre_ods_scan_file must include symlinked sibling install directories"; exit 1; }
done
unset _pre_ods_scan_file
grep -qF 'ods/ods-cli text eol=lf' ../.gitattributes \
  || { echo "[FAIL] .gitattributes must force LF checkout for extensionless ods/ods-cli"; exit 1; }

_pre_ods_guard_tmp="$(mktemp -d)"
_pre_ods_guard_harness="$_pre_ods_guard_tmp/bootstrap-guard.sh"
for _pre_ods_helper in _ods_is_install_backup_dir _ods_is_related_install_dir _ods_related_compose_containers; do
  sed -n "/^${_pre_ods_helper}() {/,/^}/p" get-ods.sh \
    > "$_pre_ods_guard_tmp/bootstrap-${_pre_ods_helper}.sh"
  sed -n "/^${_pre_ods_helper}() {/,/^}/p" installers/phases/01-preflight.sh \
    > "$_pre_ods_guard_tmp/preflight-${_pre_ods_helper}.sh"
  cmp -s \
    "$_pre_ods_guard_tmp/bootstrap-${_pre_ods_helper}.sh" \
    "$_pre_ods_guard_tmp/preflight-${_pre_ods_helper}.sh" \
    || { echo "[FAIL] Bootstrap and Linux preflight must share ${_pre_ods_helper} behavior"; rm -rf "$_pre_ods_guard_tmp"; exit 1; }
done
unset _pre_ods_helper

{
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
  printf '%s\n' 'warn() { printf "[warn] %s\n" "$*"; }'
  sed -n '/^is_truthy() {/,/^# ── Banner/p' get-ods.sh | sed '$d'
  cat <<'HARNESS_DOCKER'
docker() {
  if [[ -n "${ODS_TEST_DOCKER_ROWS_FILE:-}" && -f "$ODS_TEST_DOCKER_ROWS_FILE" ]]; then
    cat "$ODS_TEST_DOCKER_ROWS_FILE"
  fi
}
HARNESS_DOCKER
  printf '%s\n' 'refuse_legacy_install'
} > "$_pre_ods_guard_harness"
chmod +x "$_pre_ods_guard_harness"

mkdir -p "$_pre_ods_guard_tmp/home/unrelated"
touch "$_pre_ods_guard_tmp/home/unrelated/.env"
if ! PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >/dev/null 2>&1; then
  echo "[FAIL] bootstrap guard must ignore directories without the full stack signature"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi

mkdir -p "$_pre_ods_guard_tmp/older-install"
touch "$_pre_ods_guard_tmp/older-install/.env"
if PRE_ODS_INSTALL_DIR="$_pre_ods_guard_tmp/older-install" \
    ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/blocked.log" 2>&1; then
  echo "[FAIL] bootstrap guard must reject a configured older install path"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
grep -qF 'Existing related install detected' "$_pre_ods_guard_tmp/blocked.log" \
  || { echo "[FAIL] bootstrap guard rejection must explain the detected older install"; rm -rf "$_pre_ods_guard_tmp"; exit 1; }

if ! PRE_ODS_INSTALL_DIR="$_pre_ods_guard_tmp/older-install" \
    ODS_ALLOW_LEGACY_PARALLEL=1 \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >/dev/null 2>&1; then
  echo "[FAIL] bootstrap guard must honor the explicit parallel-install override"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi

mkdir -p "$_pre_ods_guard_tmp/home/related/data"
touch "$_pre_ods_guard_tmp/home/related/.env"
cat > "$_pre_ods_guard_tmp/home/related/docker-compose.base.yml" <<'COMPOSE'
services:
  llama-server:
  open-webui:
  dashboard-api:
COMPOSE
if PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/auto-path.log" 2>&1; then
  echo "[FAIL] bootstrap guard must auto-detect a dormant related install"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
grep -qF 'related install directory' "$_pre_ods_guard_tmp/auto-path.log" \
  || { echo "[FAIL] dormant related-install rejection must identify the directory"; rm -rf "$_pre_ods_guard_tmp"; exit 1; }
rm -rf "$_pre_ods_guard_tmp/home/related"

mkdir -p "$_pre_ods_guard_tmp/home/older-stack.backup-20260518-195646/data"
touch "$_pre_ods_guard_tmp/home/older-stack.backup-20260518-195646/.env"
cat > "$_pre_ods_guard_tmp/home/older-stack.backup-20260518-195646/docker-compose.base.yml" <<'COMPOSE'
services:
  llama-server:
  open-webui:
  dashboard-api:
COMPOSE
if ! PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/backup-path.log" 2>&1; then
  cat "$_pre_ods_guard_tmp/backup-path.log"
  echo "[FAIL] bootstrap guard must ignore timestamped dormant backup directories"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
rm -rf "$_pre_ods_guard_tmp/home/older-stack.backup-20260518-195646"

mkdir -p "$_pre_ods_guard_tmp/relocated/data"
touch "$_pre_ods_guard_tmp/relocated/.env"
cat > "$_pre_ods_guard_tmp/relocated/docker-compose.yml" <<'COMPOSE'
services:
  litellm:
  open-webui:
  dashboard-api:
COMPOSE
ln -s "$_pre_ods_guard_tmp/relocated" "$_pre_ods_guard_tmp/home/related-link"
if PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/auto-symlink.log" 2>&1; then
  echo "[FAIL] bootstrap guard must auto-detect a symlinked related install"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
grep -qF 'related install directory' "$_pre_ods_guard_tmp/auto-symlink.log" \
  || { echo "[FAIL] symlinked related-install rejection must identify the directory"; rm -rf "$_pre_ods_guard_tmp"; exit 1; }
rm -f "$_pre_ods_guard_tmp/home/related-link"

cat > "$_pre_ods_guard_tmp/docker-rows" <<'ROWS'
stack-llm|stack|llama-server
stack-web|stack|open-webui
stack-api|stack|dashboard-api
ROWS
if PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    ODS_TEST_DOCKER_ROWS_FILE="$_pre_ods_guard_tmp/docker-rows" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/auto-docker.log" 2>&1; then
  echo "[FAIL] bootstrap guard must auto-detect a related Compose project"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
grep -qF 'related Compose containers' "$_pre_ods_guard_tmp/auto-docker.log" \
  || { echo "[FAIL] related Compose rejection must identify the containers"; rm -rf "$_pre_ods_guard_tmp"; exit 1; }

cat > "$_pre_ods_guard_tmp/docker-rows" <<'ROWS'
stack-gateway|stack|litellm
stack-web|stack|open-webui
stack-api|stack|dashboard-api
ROWS
if PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    ODS_TEST_DOCKER_ROWS_FILE="$_pre_ods_guard_tmp/docker-rows" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >"$_pre_ods_guard_tmp/auto-gateway.log" 2>&1; then
  echo "[FAIL] bootstrap guard must auto-detect a related Compose project using the gateway service"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi

cat > "$_pre_ods_guard_tmp/docker-rows" <<'ROWS'
other-web|other-a|open-webui
other-api|other-a|dashboard-api
other-llm|other-b|llama-server
ROWS
if ! PRE_ODS_INSTALL_DIR="" ODS_ALLOW_LEGACY_PARALLEL="" \
    ODS_BOOTSTRAP_ROOT="$_pre_ods_guard_tmp/home" \
    ODS_TEST_DOCKER_ROWS_FILE="$_pre_ods_guard_tmp/docker-rows" \
    INSTALL_DIR="$_pre_ods_guard_tmp/new-install" \
    bash "$_pre_ods_guard_harness" >/dev/null 2>&1; then
  echo "[FAIL] bootstrap guard must require the complete core signature within one Compose project"
  rm -rf "$_pre_ods_guard_tmp"
  exit 1
fi
rm -rf "$_pre_ods_guard_tmp"

echo "[contract] bootstrap download finalization is non-destructive"
bash tests/test-bootstrap-upgrade-download-finalization.sh

echo "[contract] bootstrap download failures preserve resume state"
bash tests/test-bootstrap-upgrade-resume-status.sh

echo "[contract] bootstrap failed upgrades are start/restart-resumable"
grep -q 'bootstrap-upgrade.args' installers/phases/11-services.sh \
  || { echo "[FAIL] Phase 11 must persist bootstrap-upgrade retry metadata"; exit 1; }
awk '/cmd_restart\(\)/,/^}/' ods-cli | grep -q '_ods_cli_maybe_resume_bootstrap_upgrade' \
  || { echo "[FAIL] ods restart must retry failed bootstrap upgrades"; exit 1; }
awk '/cmd_start\(\)/,/^}/' ods-cli | grep -q '_ods_cli_maybe_resume_bootstrap_upgrade' \
  || { echo "[FAIL] ods start must retry failed bootstrap upgrades"; exit 1; }
awk '/cmd_restart\(\)/,/^}/' ods-cli | grep -q '_ods_cli_wait_for_bootstrap_compose_safe' \
  || { echo "[FAIL] ods restart must wait for active bootstrap hot-swaps before compose"; exit 1; }
awk '/cmd_restart\(\)/,/^}/' ods-cli | grep -q '_ods_cli_reload_model_env' \
  || { echo "[FAIL] ods restart must reload model env after bootstrap hot-swap wait"; exit 1; }
awk '/cmd_start\(\)/,/^}/' ods-cli | grep -q '_ods_cli_wait_for_bootstrap_compose_safe' \
  || { echo "[FAIL] ods start must wait for active bootstrap hot-swaps before compose"; exit 1; }
awk '/cmd_start\(\)/,/^}/' ods-cli | grep -q '_ods_cli_reload_model_env' \
  || { echo "[FAIL] ods start must reload model env after bootstrap hot-swap wait"; exit 1; }
grep -q 'starting|verifying|swapping' ods-cli \
  || { echo "[FAIL] ods-cli bootstrap compose guard must include swapping"; exit 1; }

echo "[contract] macOS host-agent LaunchAgent install-dir"
bash tests/test-macos-host-agent-verification.sh

echo "[contract] macOS direct binds replace conflicting Colima bridges"
bash tests/test-macos-direct-bind-bridge.sh

echo "[contract] macOS CLI preserves cloud/local model routing"
bash tests/test-macos-cli-mode-routing.sh

echo "[contract] macOS cloud resolver preserves selected extension state"
bash tests/test-macos-cloud-resolver.sh

echo "[contract] macOS installer preserves authenticated local/cloud transitions"
bash tests/test-macos-installer-transitions.sh

echo "[contract] AMD reassign keeps HSA override Strix-only"
grep -q '_env_set "HSA_OVERRIDE_GFX_VERSION" "11.5.1"' ods-cli \
  || { echo "[FAIL] ods-cli must set HSA override to 11.5.1 for gfx1151"; exit 1; }
grep -q '_env_unset "HSA_OVERRIDE_GFX_VERSION"' ods-cli \
  || { echo "[FAIL] ods-cli must remove HSA override for non-Strix AMD GPUs"; exit 1; }
grep -q '_env_unset "LEMONADE_LLAMACPP_ROCM_BIN"' ods-cli \
  || { echo "[FAIL] ods-cli must remove gfx1151-only custom binary for non-Strix AMD GPUs"; exit 1; }
if grep -q '_env_set "HSA_OVERRIDE_GFX_VERSION" "\$gfx_ver"' ods-cli; then
  echo "[FAIL] ods-cli must not write raw gfx ids such as gfx942 to HSA_OVERRIDE_GFX_VERSION"
  exit 1
fi

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

echo "[contract] dashboard nginx re-resolves dashboard-api after lifecycle churn"
dashboard_nginx="extensions/services/dashboard/nginx.conf"
grep -qF 'resolver 127.0.0.11' "$dashboard_nginx" \
  || { echo "[FAIL] dashboard nginx must use Docker DNS resolver"; exit 1; }
grep -qF 'set $dashboard_api_upstream dashboard-api:3002;' "$dashboard_nginx" \
  || { echo "[FAIL] dashboard nginx must proxy through a variable upstream"; exit 1; }
grep -qF 'proxy_pass http://$dashboard_api_upstream;' "$dashboard_nginx" \
  || { echo "[FAIL] dashboard nginx /api locations must use the dynamic upstream"; exit 1; }
if grep -qF 'proxy_pass http://dashboard-api:3002;' "$dashboard_nginx"; then
  echo "[FAIL] dashboard nginx must not pin dashboard-api at config-load time"
  exit 1
fi

echo "[contract] bundled service CPU limits are env-driven"
grep -qF "cpus: '\${TTS_CPU_LIMIT:-1.0}'" extensions/services/tts/compose.yaml \
  || { echo "[FAIL] Kokoro TTS CPU limit must be env-driven with safe fallback"; exit 1; }
grep -qF "cpus: '\${WHISPER_CPU_LIMIT:-1.0}'" extensions/services/whisper/compose.yaml \
  || { echo "[FAIL] Whisper CPU limit must be env-driven with safe fallback"; exit 1; }
grep -qF "cpus: '\${WHISPER_CPU_LIMIT:-1.0}'" extensions/services/whisper/compose.nvidia.yaml \
  || { echo "[FAIL] Whisper NVIDIA CPU limit must be env-driven with safe fallback"; exit 1; }
grep -qF "cpus: '\${HERMES_CPU_LIMIT:-1.0}'" extensions/services/hermes/compose.yaml \
  || { echo "[FAIL] Hermes CPU limit must be env-driven with safe fallback"; exit 1; }
grep -qF "cpus: '\${COMFYUI_CPU_LIMIT:-1.0}'" extensions/services/comfyui/compose.nvidia.yaml \
  || { echo "[FAIL] ComfyUI NVIDIA CPU limit must be env-driven with safe fallback"; exit 1; }
grep -qF "cpus: '\${COMFYUI_CPU_LIMIT:-1.0}'" extensions/services/comfyui/compose.amd.yaml \
  || { echo "[FAIL] ComfyUI AMD CPU limit must be env-driven with safe fallback"; exit 1; }
for key in TTS_CPU_LIMIT TTS_CPU_RESERVATION WHISPER_CPU_LIMIT WHISPER_CPU_RESERVATION HERMES_CPU_LIMIT HERMES_CPU_RESERVATION COMFYUI_CPU_LIMIT COMFYUI_CPU_RESERVATION; do
  jq -e --arg key "$key" '.properties[$key]' .env.schema.json >/dev/null \
    || { echo "[FAIL] .env.schema.json missing bundled service CPU key: $key"; exit 1; }
done

echo "[contract] resolver scripts executable"
for s in scripts/build-capability-profile.sh scripts/classify-hardware.sh scripts/load-backend-contract.sh scripts/resolve-compose-stack.sh scripts/preflight-engine.sh scripts/ods-doctor.sh scripts/simulate-installers.sh; do
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

echo "[contract] RAG service flags gate qdrant and embeddings"
# RAG = qdrant (vector store) + embeddings (TEI). Both default from
# ENABLE_RAG, then host-specific guards can disable the concrete service
# when an upstream image cannot run on that machine.
features_phase="ods/installers/phases/03-features.sh"
test -f "$features_phase" || features_phase="installers/phases/03-features.sh"
test -f "$features_phase" || { echo "[FAIL] cannot locate 03-features.sh"; exit 1; }
grep -q 'ENABLE_QDRANT="${ENABLE_QDRANT:-${ENABLE_RAG:-false}}"' "$features_phase" \
  || { echo "[FAIL] ENABLE_QDRANT does not default from ENABLE_RAG in $features_phase"; exit 1; }
grep -q 'ENABLE_EMBEDDINGS="${ENABLE_EMBEDDINGS:-${ENABLE_RAG:-false}}"' "$features_phase" \
  || { echo "[FAIL] ENABLE_EMBEDDINGS does not default from ENABLE_RAG in $features_phase"; exit 1; }
grep -qE '_sync_extension_compose +"\$\{ENABLE_QDRANT:-\$\{ENABLE_RAG:-false\}\}" +qdrant\b' "$features_phase" \
  || { echo "[FAIL] Qdrant compose is not gated by ENABLE_QDRANT in $features_phase"; exit 1; }
grep -qE '_sync_extension_compose +"\$\{ENABLE_EMBEDDINGS:-\$\{ENABLE_RAG:-false\}\}" +embeddings\b' "$features_phase" \
  || { echo "[FAIL] Embeddings compose is not gated by ENABLE_EMBEDDINGS in $features_phase"; exit 1; }
grep -q 'HOST_PAGE_SIZE:-$(getconf PAGE_SIZE' "$features_phase" \
  || { echo "[FAIL] Qdrant arm64 page-size guard missing from $features_phase"; exit 1; }
for f in installers/phases/04-requirements.sh installers/phases/08-images.sh installers/phases/12-health.sh installers/phases/13-summary.sh; do
  test -f "$f" || { echo "[FAIL] missing installer phase: $f"; exit 1; }
  grep -q 'ENABLE_QDRANT:-${ENABLE_RAG:-false}' "$f" \
    || { echo "[FAIL] $f still gates Qdrant on ENABLE_RAG directly"; exit 1; }
done

run_phase03_rag_guard() {
  local arch="$1" page_size="$2" tmpdir
  tmpdir="$(mktemp -d)"
  mkdir -p "$tmpdir/extensions/services/qdrant" "$tmpdir/extensions/services/embeddings"
  printf 'services: {}\n' >"$tmpdir/extensions/services/qdrant/compose.yaml"
  printf 'services: {}\n' >"$tmpdir/extensions/services/embeddings/compose.yaml"

  (
    set -euo pipefail
    INTERACTIVE=false
    DRY_RUN=false
    INSTALL_CHOICE=1
    TIER=1
    ODS_MODE=local
    ENABLE_RAG=true
    ENABLE_HERMES=false
    ENABLE_OPENCLAW=false
    ENABLE_COMFYUI=false
    ENABLE_WORKFLOWS=false
    ENABLE_VOICE=false
    GPU_COUNT=1
    GPU_BACKEND=cpu
    HOST_ARCH="$arch"
    HOST_PAGE_SIZE="$page_size"
    SCRIPT_DIR="$tmpdir"
    INSTALL_DIR="$tmpdir/install"
    MAX_CONTEXT=4096
    LLM_MODEL_SIZE_MB=0

    ods_progress() { :; }
    ai_warn() { :; }
    log() { :; }
    warn() { :; }
    success() { :; }
    chapter() { :; }
    bootline() { :; }
    signal() { :; }
    show_phase() { :; }
    show_install_menu() { :; }

    # shellcheck source=/dev/null
    source "$features_phase" >/dev/null

    printf 'ENABLE_QDRANT=%s\n' "${ENABLE_QDRANT:-}"
    printf 'ENABLE_EMBEDDINGS=%s\n' "${ENABLE_EMBEDDINGS:-}"
    if [[ -f "$tmpdir/extensions/services/qdrant/compose.yaml" ]]; then
      printf 'QDRANT_COMPOSE=enabled\n'
    elif [[ -f "$tmpdir/extensions/services/qdrant/compose.yaml.disabled" ]]; then
      printf 'QDRANT_COMPOSE=disabled\n'
    else
      printf 'QDRANT_COMPOSE=missing\n'
    fi
    if [[ -f "$tmpdir/extensions/services/embeddings/compose.yaml" ]]; then
      printf 'EMBEDDINGS_COMPOSE=enabled\n'
    elif [[ -f "$tmpdir/extensions/services/embeddings/compose.yaml.disabled" ]]; then
      printf 'EMBEDDINGS_COMPOSE=disabled\n'
    else
      printf 'EMBEDDINGS_COMPOSE=missing\n'
    fi
  )

  rm -rf "$tmpdir"
}

guard_64k="$(run_phase03_rag_guard aarch64 65536)"
echo "$guard_64k" | grep -q '^ENABLE_QDRANT=false$' \
  || { echo "[FAIL] 64K-page aarch64 must disable ENABLE_QDRANT"; echo "$guard_64k"; exit 1; }
echo "$guard_64k" | grep -q '^QDRANT_COMPOSE=disabled$' \
  || { echo "[FAIL] 64K-page aarch64 must disable Qdrant compose"; echo "$guard_64k"; exit 1; }

guard_4k="$(run_phase03_rag_guard aarch64 4096)"
echo "$guard_4k" | grep -q '^ENABLE_QDRANT=true$' \
  || { echo "[FAIL] 4K-page aarch64 must keep ENABLE_QDRANT enabled"; echo "$guard_4k"; exit 1; }
echo "$guard_4k" | grep -q '^QDRANT_COMPOSE=enabled$' \
  || { echo "[FAIL] 4K-page aarch64 must keep Qdrant compose enabled"; echo "$guard_4k"; exit 1; }
echo "$guard_4k" | grep -q '^ENABLE_EMBEDDINGS=false$' \
  || { echo "[FAIL] aarch64 must still disable amd64-only embeddings"; echo "$guard_4k"; exit 1; }
echo "$guard_4k" | grep -q '^EMBEDDINGS_COMPOSE=disabled$' \
  || { echo "[FAIL] aarch64 must still disable embeddings compose"; echo "$guard_4k"; exit 1; }

echo "[contract] non-interactive reinstall reuses valid GPU assignment"
grep -q '_load_existing_gpu_assignment_json' "$features_phase" \
  || { echo "[FAIL] multi-GPU reinstall must load existing GPU assignment"; exit 1; }
grep -q 'GPU_ASSIGNMENT_JSON_B64' "$features_phase" \
  || { echo "[FAIL] multi-GPU reinstall must read persisted GPU_ASSIGNMENT_JSON_B64"; exit 1; }
grep -q 'Reusing existing GPU assignment from .env' "$features_phase" \
  || { echo "[FAIL] multi-GPU reinstall must log assignment reuse"; exit 1; }

echo "[contract] every resolve-compose-stack.sh invocation passes --gpu-count"
# The resolver's --gpu-count flag gates the multigpu-{backend}.yml overlay.
# A caller that omits it silently resolves to a single-GPU stack on multi-GPU
# hardware. 11-services.sh persists its result into .compose-flags, so the
# bug propagates to every subsequent ods-cli invocation.
#
# Strategy: pair each line invoking the resolver with the next 3 lines (to
# catch backslash-continued invocations) and assert that segment contains
# --gpu-count. Existence guards like [[ -x ... ]] are excluded — they don't
# launch the script.
_resolver_callers=(
  "ods-cli"
  "ods-update.sh"
  "bin/ods-host-agent.py"
  "scripts/ods-preflight.sh"
  "scripts/validate.sh"
  "installers/lib/compose-select.sh"
  "installers/macos/ods-macos.sh"
  "installers/phases/03-features.sh"
  "installers/phases/11-services.sh"
)
for f in "${_resolver_callers[@]}"; do
  test -f "$f" || { echo "[FAIL] missing resolver caller: $f"; exit 1; }
  # Match lines that actually launch the script: $(...resolve-compose-stack.sh...
  # or "...resolve-compose-stack.sh" \  or bash ...resolve-compose-stack.sh...
  # Skip lines whose 'resolve-compose-stack.sh' is a [[ -x|-f ]] existence test.
  while IFS=: read -r lineno line; do
    # Skip existence guards ([[ -x ... ]], [[ -f ... ]]) and comments/docstrings.
    [[ "$line" =~ \[\[[[:space:]]+-[xfre][[:space:]] ]] && continue
    [[ "$line" =~ ^[[:space:]]*\# ]] && continue
    [[ "$line" =~ ^[[:space:]]*(\"|\') ]] && continue
    end=$((lineno + 8))
    segment=$(sed -n "${lineno},${end}p" "$f")
    if ! grep -q -- "--gpu-count" <<<"$segment"; then
      echo "[FAIL] $f:$lineno invokes resolver without --gpu-count nearby"
      exit 1
    fi
  done < <(grep -nE 'resolve-compose-stack\.sh' "$f" || true)
done
unset _resolver_callers

echo "[contract] optional extension compose files are installer-gated"
# Bundled optional/recommended services that ship compose.yaml must not enter
# a Core Only install just because their compose file exists in the source tree.
for spec in \
  'ENABLE_RECOMMENDED:litellm' \
  'ENABLE_RECOMMENDED:searxng' \
  'ENABLE_RECOMMENDED:token-spy' \
  'ENABLE_HERMES:hermes' \
  'ENABLE_HERMES:hermes-proxy' \
  'ENABLE_OPENCLAW:openclaw' \
  'ENABLE_APE:ape' \
  'ENABLE_PERPLEXICA:perplexica' \
  'ENABLE_PRIVACY_SHIELD:privacy-shield' \
  'ENABLE_ODS_PROXY:ods-proxy' \
  'ENABLE_TAILSCALE:tailscale' \
  'ENABLE_BRAVE_SEARCH:brave-search'
do
  flag="${spec%%:*}"
  svc="${spec##*:}"
  grep -qE "_sync_extension_compose +\"\\\$\\{${flag}:-[^}]*\\}\" +$svc\\b|_sync_extension_compose +\"\\\$\\{${flag}:-\\}\" +$svc\\b" "$features_phase" \
    || { echo "[FAIL] $svc compose is not gated by $flag in $features_phase"; exit 1; }
done

windows_plan="installers/windows/lib/service-plan.ps1"
test -f "$windows_plan" || { echo "[FAIL] missing $windows_plan"; exit 1; }
for svc in litellm searxng token-spy hermes hermes-proxy openclaw ape perplexica privacy-shield ods-proxy tailscale brave-search; do
  grep -q "\"$svc\"" "$windows_plan" \
    || { echo "[FAIL] Windows service plan missing '$svc'"; exit 1; }
done

echo "[contract] Linux local rebuilds respect selected compose services"
grep -q 'config --services' installers/phases/11-services.sh \
  || { echo "[FAIL] Linux installer must inspect selected compose services before local rebuilds"; exit 1; }
grep -q 'Skipping local image build for disabled service' installers/phases/11-services.sh \
  || { echo "[FAIL] Linux installer must skip disabled local-build services"; exit 1; }
if grep -q '^[[:space:]]*_build_services=(dashboard dashboard-api ape token-spy privacy-shield)' installers/phases/11-services.sh; then
  echo "[FAIL] Linux installer must not build every local service unconditionally"
  exit 1
fi

echo "[contract] failed requested local builds cannot reuse stale images"
bash tests/test-phase11-local-build-failure.sh

echo "[contract] OpenClaw deprecation preserves actual installs only"
for installer in install-core.sh installers/macos/install-macos.sh; do
  grep -Fq 'name=^/ods-openclaw$' "$installer" \
    || { echo "[FAIL] $installer must preserve OpenClaw when a prior container exists"; exit 1; }
  grep -Fq 'data/openclaw' "$installer" \
    || { echo "[FAIL] $installer must preserve OpenClaw when persisted data exists"; exit 1; }
  installer_code="$(sed '/^[[:space:]]*#/d' "$installer")"
  if grep -Fq 'extensions/services/openclaw/compose.yaml' <<<"$installer_code"; then
    echo "[FAIL] $installer must not auto-enable OpenClaw just because the bundled compose file exists"
    exit 1
  fi
  unset installer_code
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

# --- classify-hardware: shared device_id disambiguation ---
echo "[contract] classify-hardware shared device_id"
_classify() {
  bash scripts/classify-hardware.sh --device-id "$1" --gpu-name "$2" --gpu-vendor "${3:-amd}" --vram-mb "${4:-0}" --memory-type "${5:-discrete}" 2>/dev/null
}
_classify_id()   { _classify "$@" | jq -r '.id'; }
_classify_tier() { _classify "$@" | jq -r '.recommended.tier'; }
_classify_backend() { _classify "$@" | jq -r '.recommended.backend'; }
_classify_bw()   { _classify "$@" | jq -r '.bandwidth_gbps'; }

# --- Low-VRAM NVIDIA cards must not be routed to CUDA by default ---

[[ "$(_classify_id "" "NVIDIA GeForce 940MX" nvidia 2048)" == "nvidia_low_vram_cpu_fallback" ]] \
  || { echo "[FAIL] 2GB NVIDIA must match low-VRAM CPU fallback"; exit 1; }
[[ "$(_classify_backend "" "NVIDIA GeForce 940MX" nvidia 2048)" == "cpu" ]] \
  || { echo "[FAIL] 2GB NVIDIA must use CPU backend"; exit 1; }
[[ "$(_classify_tier "" "NVIDIA GeForce 940MX" nvidia 2048)" == "T0" ]] \
  || { echo "[FAIL] 2GB NVIDIA must use T0"; exit 1; }
[[ "$(_classify_id "" "NVIDIA GeForce GTX 1650" nvidia 4096)" == "nvidia_entry" ]] \
  || { echo "[FAIL] 4GB NVIDIA should remain entry CUDA"; exit 1; }
[[ "$(_classify_backend "" "NVIDIA GeForce GTX 1650" nvidia 4096)" == "nvidia" ]] \
  || { echo "[FAIL] 4GB NVIDIA should keep NVIDIA backend"; exit 1; }

# --- 0x744c: XTX / XT / GRE (same die, different SKUs) ---

# Happy path: device_id + name → exact match
[[ "$(_classify_id 0x744c "AMD Radeon RX 7900 XTX" amd 24576)" == "rx_7900_xtx" ]] \
  || { echo "[FAIL] XTX with name"; exit 1; }
[[ "$(_classify_id 0x744c "AMD Radeon RX 7900 XT" amd 20480)" == "rx_7900_xt" ]] \
  || { echo "[FAIL] XT with name"; exit 1; }
[[ "$(_classify_id 0x744c "AMD Radeon RX 7900 GRE" amd 16384)" == "rx_7900_gre" ]] \
  || { echo "[FAIL] GRE with name"; exit 1; }

# Substring safety: "RX 7900 XT" is a substring of "RX 7900 XTX"
# XT name must NOT match XTX entry (longest pattern wins)
[[ "$(_classify_id 0x744c "AMD Radeon RX 7900 XT" amd 20480)" != "rx_7900_xtx" ]] \
  || { echo "[FAIL] XT matched XTX (substring collision)"; exit 1; }
# XTX name must NOT match XT entry
[[ "$(_classify_id 0x744c "AMD Radeon RX 7900 XTX" amd 24576)" != "rx_7900_xt" ]] \
  || { echo "[FAIL] XTX matched XT"; exit 1; }

# Tier correctness: GRE is T2, the others are T3
[[ "$(_classify_tier 0x744c "AMD Radeon RX 7900 XTX" amd 24576)" == "T3" ]] \
  || { echo "[FAIL] XTX tier"; exit 1; }
[[ "$(_classify_tier 0x744c "AMD Radeon RX 7900 GRE" amd 16384)" == "T2" ]] \
  || { echo "[FAIL] GRE tier"; exit 1; }

# Bandwidth correctness: each SKU has a different value
[[ "$(_classify_bw 0x744c "AMD Radeon RX 7900 XTX" amd 24576)" == "960" ]] \
  || { echo "[FAIL] XTX bandwidth"; exit 1; }
[[ "$(_classify_bw 0x744c "AMD Radeon RX 7900 XT" amd 20480)" == "800" ]] \
  || { echo "[FAIL] XT bandwidth"; exit 1; }
[[ "$(_classify_bw 0x744c "AMD Radeon RX 7900 GRE" amd 16384)" == "576" ]] \
  || { echo "[FAIL] GRE bandwidth"; exit 1; }

# Empty name: VRAM tiebreaker picks closest match
[[ "$(_classify_id 0x744c "" amd 24576)" == "rx_7900_xtx" ]] \
  || { echo "[FAIL] empty name + 24GB → XTX"; exit 1; }
[[ "$(_classify_id 0x744c "" amd 20480)" == "rx_7900_xt" ]] \
  || { echo "[FAIL] empty name + 20GB → XT"; exit 1; }
[[ "$(_classify_id 0x744c "" amd 16384)" == "rx_7900_gre" ]] \
  || { echo "[FAIL] empty name + 16GB → GRE"; exit 1; }

# Empty name + zero VRAM: picks smallest card (under-provision is safe,
# over-provision would crash the model loader)
[[ "$(_classify_id 0x744c "" amd 0)" == "rx_7900_gre" ]] \
  || { echo "[FAIL] empty name + 0 VRAM → should be GRE (smallest)"; exit 1; }

# Empty name + close-but-not-exact VRAM: picks nearest
# 22000 MB is closer to XT (20480, diff=1520) than XTX (24576, diff=2576)
[[ "$(_classify_id 0x744c "" amd 22000)" == "rx_7900_xt" ]] \
  || { echo "[FAIL] empty name + 22GB → should be XT (nearest)"; exit 1; }
# 18000 MB is closer to GRE (16384, diff=1616) than XT (20480, diff=2480)
[[ "$(_classify_id 0x744c "" amd 18000)" == "rx_7900_gre" ]] \
  || { echo "[FAIL] empty name + 18GB → should be GRE (nearest)"; exit 1; }

# --- 0x7480: RX 7800 XT / RX 7700 XT (second shared device_id pair) ---

[[ "$(_classify_id 0x7480 "AMD Radeon RX 7800 XT" amd 16384)" == "rx_7800_xt" ]] \
  || { echo "[FAIL] 7800 XT with name"; exit 1; }
[[ "$(_classify_id 0x7480 "AMD Radeon RX 7700 XT" amd 12288)" == "rx_7700_xt" ]] \
  || { echo "[FAIL] 7700 XT with name"; exit 1; }
[[ "$(_classify_id 0x7480 "" amd 16384)" == "rx_7800_xt" ]] \
  || { echo "[FAIL] 0x7480 empty name + 16GB → 7800 XT"; exit 1; }
[[ "$(_classify_id 0x7480 "" amd 12288)" == "rx_7700_xt" ]] \
  || { echo "[FAIL] 0x7480 empty name + 12GB → 7700 XT"; exit 1; }

# --- Name-only match (no device_id) ---

[[ "$(_classify_id "" "RYZEN AI MAX+ 395" amd 0)" == "strix_halo_395" ]] \
  || { echo "[FAIL] Strix Halo name-only match"; exit 1; }
[[ "$(_classify_id "" "RX 9070 XT" amd 16384)" == "rx_9070_xt" ]] \
  || { echo "[FAIL] RX 9070 XT name-only match"; exit 1; }

# --- No match → heuristic fallback (should not crash) ---

result=$(_classify_id "0xFFFF" "Unknown GPU" amd 8192)
[[ -n "$result" && "$result" != "null" ]] \
  || { echo "[FAIL] unknown GPU crashed"; exit 1; }

echo "[contract] macOS compose resolver installs PyYAML into an isolated selected-Python venv"
grep -q '_ensure_macos_pyyaml' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not use the PyYAML readiness helper"; exit 1; }
grep -q 'python-cmd.sh' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not load the shared Python resolver"; exit 1; }
grep -q '_macos_python_imports_yaml "$pycmd"' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not verify PyYAML with the selected Python"; exit 1; }
grep -q '"$pycmd" -m venv "$venv_dir"' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must create the PyYAML venv with the selected Python"; exit 1; }
grep -q '_set_installer_python_cmd "$venv_python"' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must route compose resolver to the venv Python"; exit 1; }
if grep -q 'pip install --user .*pyyaml\|pip install .*--user .*pyyaml' installers/macos/install-macos.sh; then
  echo "[FAIL] macOS installer must not use pip --user for PyYAML; Homebrew Python rejects it under PEP 668"
  exit 1
fi
grep -q 'export ODS_PYTHON_CMD' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer does not export the selected Python for resolver scripts"; exit 1; }

echo "[contract] macOS OpenCode uses discoverable binary path"
grep -q 'type -P opencode' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must resolve an executable OpenCode file, not a shell function/alias"; exit 1; }
grep -q 'brew --prefix' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must check the Homebrew prefix for OpenCode"; exit 1; }
grep -q '_opencode_candidate_is_file' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must validate resolved OpenCode as an absolute executable file"; exit 1; }
grep -q 'brew install opencode' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer should prefer Homebrew OpenCode when brew is available"; exit 1; }
grep -q '<string>${OPENCODE_BIN}</string>' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS OpenCode LaunchAgent must use resolved OPENCODE_BIN"; exit 1; }
grep -q '_compute_launchd_path "$(dirname "$OPENCODE_BIN")"' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS OpenCode LaunchAgent PATH must include resolved binary directory"; exit 1; }

echo "[contract] macOS local rebuilds respect selected compose services"
grep -q 'config --services' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must inspect selected compose services before local rebuilds"; exit 1; }
grep -q 'Could not resolve macOS compose services for local image rebuilds' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must fail clearly if compose service resolution fails"; exit 1; }
grep -q 'Skipping local image rebuild for disabled service' installers/macos/install-macos.sh \
  || { echo "[FAIL] macOS installer must skip disabled local-build services"; exit 1; }
if grep -q '_macos_build_services=(dashboard dashboard-api ape token-spy privacy-shield)' installers/macos/install-macos.sh; then
  echo "[FAIL] macOS installer must not rebuild every local service unconditionally"
  exit 1
fi

echo "[contract] Hermes context defaults are installer-wide"
bash tests/test-installer-context-parity.sh

echo "[contract] Linux installer/background model lifecycle serialization"
bash tests/test-linux-installer-model-lifecycle-lock.sh

echo "[PASS] installer contracts"
