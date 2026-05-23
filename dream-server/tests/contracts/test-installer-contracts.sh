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

echo "[contract] bootstrap hot-swap force-recreate"
bash tests/test-bootstrap-upgrade-hotswap-contract.sh

echo "[contract] AMD reassign keeps HSA override Strix-only"
grep -q '_env_set "HSA_OVERRIDE_GFX_VERSION" "11.5.1"' dream-cli \
  || { echo "[FAIL] dream-cli must set HSA override to 11.5.1 for gfx1151"; exit 1; }
grep -q '_env_unset "HSA_OVERRIDE_GFX_VERSION"' dream-cli \
  || { echo "[FAIL] dream-cli must remove HSA override for non-Strix AMD GPUs"; exit 1; }
grep -q '_env_unset "LEMONADE_LLAMACPP_ROCM_BIN"' dream-cli \
  || { echo "[FAIL] dream-cli must remove gfx1151-only custom binary for non-Strix AMD GPUs"; exit 1; }
if grep -q '_env_set "HSA_OVERRIDE_GFX_VERSION" "\$gfx_ver"' dream-cli; then
  echo "[FAIL] dream-cli must not write raw gfx ids such as gfx942 to HSA_OVERRIDE_GFX_VERSION"
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

echo "[contract] every resolve-compose-stack.sh invocation passes --gpu-count"
# The resolver's --gpu-count flag gates the multigpu-{backend}.yml overlay.
# A caller that omits it silently resolves to a single-GPU stack on multi-GPU
# hardware. 11-services.sh persists its result into .compose-flags, so the
# bug propagates to every subsequent dream-cli invocation.
#
# Strategy: pair each line invoking the resolver with the next 3 lines (to
# catch backslash-continued invocations) and assert that segment contains
# --gpu-count. Existence guards like [[ -x ... ]] are excluded — they don't
# launch the script.
_resolver_callers=(
  "dream-cli"
  "dream-update.sh"
  "bin/dream-host-agent.py"
  "scripts/dream-preflight.sh"
  "scripts/validate.sh"
  "installers/lib/compose-select.sh"
  "installers/macos/dream-macos.sh"
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
  'ENABLE_DREAM_PROXY:dream-proxy' \
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
for svc in litellm searxng token-spy hermes hermes-proxy openclaw ape perplexica privacy-shield dream-proxy tailscale brave-search; do
  grep -q "\"$svc\"" "$windows_plan" \
    || { echo "[FAIL] Windows service plan missing '$svc'"; exit 1; }
done

echo "[contract] OpenClaw deprecation preserves actual installs only"
for installer in install-core.sh installers/macos/install-macos.sh; do
  grep -Fq 'name=^/dream-openclaw$' "$installer" \
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
  bash scripts/classify-hardware.sh --device-id "$1" --gpu-name "$2" --gpu-vendor "${3:-amd}" --vram-mb "${4:-0}" 2>/dev/null
}
_classify_id()   { _classify "$@" | jq -r '.id'; }
_classify_tier() { _classify "$@" | jq -r '.recommended.tier'; }
_classify_bw()   { _classify "$@" | jq -r '.bandwidth_gbps'; }

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
grep -q 'export DREAM_PYTHON_CMD' installers/macos/install-macos.sh \
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

echo "[contract] Hermes context defaults are installer-wide"
bash tests/test-installer-context-parity.sh

echo "[PASS] installer contracts"
