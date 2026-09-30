#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Compatibility Fixes
# ============================================================================
# Part of: p2p-gpu/lib/
# Purpose: Service-specific compatibility patches for Whisper, TTS, ComfyUI,
#          and OpenClaw running on Vast.ai instances
#
# Expects: LOGFILE, log(), warn(), env_get(), wait_for_http()
# Provides: ensure_whisper_ui_compatibility(), ensure_webui_stt_model_alignment(),
#           map_whisper_model_id(), ensure_whisper_asr_model(), ensure_tts_model_ready(),
#           fix_comfyui_permissions(), comfyui_preload_models(),
#           patch_openclaw_inject_token_runtime()
#
# Modder notes:
#   These are narrow fixes for known Vast.ai failure modes. Each function
#   is idempotent and safe to re-run.
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

# Fix Whisper UI internal API connectivity + entrypoint executable bit
ensure_whisper_ui_compatibility() {
  local ds_dir="$1"
  local whisper_compose="${ds_dir}/extensions/services/whisper/compose.yaml"
  local whisper_entrypoint="${ds_dir}/extensions/services/whisper/docker-entrypoint.sh"

  if [[ -f "$whisper_entrypoint" ]]; then
    # [NON-FATAL: whisper] Entry point permissions only affect Whisper UI.
    chmod 755 "$whisper_entrypoint" || warn "whisper entrypoint chmod failed (non-fatal)"
  fi

  [[ ! -f "$whisper_compose" ]] && return 0

  if ! grep -q 'LOOPBACK_HOST_URL=' "$whisper_compose"; then
    if grep -q 'WHISPER__TTL=' "$whisper_compose"; then
      sed -i '/WHISPER__TTL=/a\      - LOOPBACK_HOST_URL=http://127.0.0.1:8000\n      - CHAT_COMPLETION_BASE_URL=http://llama-server:8080/v1\n      - CHAT_COMPLETION_API_KEY=cant-be-empty' \
        "$whisper_compose"
      log "Injected Whisper UI loopback compatibility env"
    else
      warn "Whisper compose env block not found — skipped loopback injection"
    fi
  fi
}

# Keep Open WebUI STT model aligned with the Whisper model we bootstrap.
# Fixes mismatch where WebUI requests a model that Whisper does not have.
ensure_webui_stt_model_alignment() {
  local ds_dir="$1"
  local env_file="${ds_dir}/.env"
  local nvidia_overlay="${ds_dir}/docker-compose.nvidia.yml"
  [[ ! -f "$nvidia_overlay" ]] && return 0

  local whisper_cfg model_id current
  whisper_cfg="$(env_get "$env_file" "WHISPER_MODEL")"
  model_id="$(map_whisper_model_id "$whisper_cfg")"
  [[ -z "$model_id" ]] && model_id="Systran/faster-whisper-base"

  current=$(grep -E 'AUDIO_STT_MODEL:' "$nvidia_overlay" | head -1 | sed -E 's/.*AUDIO_STT_MODEL:\s*"?(.*)"?/\1/' || echo "")
  [[ "$current" == "$model_id" ]] && return 0

  # Preserve existing indentation to avoid corrupting YAML structure.
  sed -i -E "s|^([[:space:]]*)AUDIO_STT_MODEL:.*|\1AUDIO_STT_MODEL: \"${model_id}\"|" "$nvidia_overlay"
  log "Aligned Open WebUI STT model to ${model_id}"
}

# Map friendly WHISPER_MODEL values to Speaches-compatible model IDs
map_whisper_model_id() {
  local raw="$1"
  case "${raw,,}" in
    tiny|tiny.en)                echo "Systran/faster-whisper-tiny" ;;
    base|base.en|"")             echo "Systran/faster-whisper-base" ;;
    small|small.en)              echo "Systran/faster-whisper-small" ;;
    medium|medium.en)            echo "Systran/faster-whisper-medium" ;;
    large|large-v2|large-v3)     echo "Systran/faster-whisper-large-v3" ;;
    turbo|large-v3-turbo)        echo "deepdml/faster-whisper-large-v3-turbo-ct2" ;;
    */*)                         echo "$raw" ;;
    *)                           echo "Systran/faster-whisper-base" ;;
  esac
}

# Ensure at least one ASR model is loaded in Whisper
ensure_whisper_asr_model() {
  local ds_dir="$1"
  local env_file="${ds_dir}/.env"
  local whisper_port
  whisper_port="$(env_get "$env_file" "WHISPER_PORT")"
  whisper_port="${whisper_port:-9000}"

  if ! wait_for_http "http://127.0.0.1:${whisper_port}/health" 120 4; then
    warn "Whisper not reachable on port ${whisper_port} — skipping ASR bootstrap"
    return 0
  fi

  local asr_count
  asr_count=$(curl -sf --max-time 12 \
    "http://127.0.0.1:${whisper_port}/v1/models?task=automatic-speech-recognition" \
    | jq -r '.data | length' || echo 0)

  if [[ "$asr_count" =~ ^[0-9]+$ ]] && [[ "$asr_count" -gt 0 ]]; then
    log "Whisper ASR models already available (${asr_count})"
    return 0
  fi

  local whisper_cfg model_id encoded_model
  whisper_cfg="$(env_get "$env_file" "WHISPER_MODEL")"
  model_id="$(map_whisper_model_id "$whisper_cfg")"
  encoded_model="${model_id//\//%2F}"

  warn "No ASR models — bootstrapping ${model_id}"
  curl -sf -X POST --max-time 30 \
    "http://127.0.0.1:${whisper_port}/v1/models/${encoded_model}" > /dev/null \
    || { warn "Could not trigger Whisper model download for ${model_id}"; return 0; }

  _wait_for_asr "$whisper_port"
}

_wait_for_asr() {
  local whisper_port="$1"
  local waited=0
  while [[ $waited -lt 180 ]]; do
    local asr_count
    asr_count=$(curl -sf --max-time 12 \
      "http://127.0.0.1:${whisper_port}/v1/models?task=automatic-speech-recognition" \
      | jq -r '.data | length' || echo 0)
    if [[ "$asr_count" =~ ^[0-9]+$ ]] && [[ "$asr_count" -gt 0 ]]; then
      log "Whisper ASR model bootstrap complete (${asr_count} model(s))"
      return 0
    fi
    sleep 6
    waited=$((waited + 6))
  done
  warn "Whisper model download started but not ready — will appear shortly"
}

# Wait for Kokoro TTS to load at least one voice model
ensure_tts_model_ready() {
  local ds_dir="$1"
  local env_file="${ds_dir}/.env"
  local tts_port
  tts_port="$(env_get "$env_file" "TTS_PORT")"
  tts_port="${tts_port:-8880}"

  if ! docker ps --format '{{.Names}}' | grep -q 'ods-tts'; then
    return 0
  fi

  if ! wait_for_http "http://127.0.0.1:${tts_port}/health" 90 4; then
    warn "Kokoro TTS not reachable on port ${tts_port} — skipping"
    return 0
  fi

  local voice_count
  voice_count=$(curl -sf --max-time 10 "http://127.0.0.1:${tts_port}/v1/audio/voices" \
    | jq -r 'if type == "array" then length elif .voices then (.voices | length) else 0 end' \
    || echo 0)

  if [[ "$voice_count" =~ ^[0-9]+$ ]] && [[ "$voice_count" -gt 0 ]]; then
    log "Kokoro TTS ready (${voice_count} voice(s))"
    return 0
  fi

  warn "Kokoro TTS starting — waiting for voice model..."
  _wait_for_tts "$tts_port"
}

_wait_for_tts() {
  local tts_port="$1"
  local waited=0
  while [[ $waited -lt 90 ]]; do
    local voice_count
    voice_count=$(curl -sf --max-time 10 "http://127.0.0.1:${tts_port}/v1/models" \
      | jq -r '.data | length' || echo 0)
    if [[ "$voice_count" =~ ^[0-9]+$ ]] && [[ "$voice_count" -gt 0 ]]; then
      log "Kokoro TTS model loaded (${voice_count} model(s))"
      return 0
    fi
    sleep 6
    waited=$((waited + 6))
  done
  warn "Kokoro TTS model still loading — will be available shortly"
}

# Fix ComfyUI permissions for AMD vs NVIDIA mount layouts
fix_comfyui_permissions() {
  local data_dir="$1"
  local gpu_backend="${2:-nvidia}"

  local dirs
  if [[ "$gpu_backend" == "amd" ]]; then
    dirs=("${data_dir}/comfyui/ComfyUI/models"
          "${data_dir}/comfyui/ComfyUI/output"
          "${data_dir}/comfyui/ComfyUI/input"
          "${data_dir}/comfyui/ComfyUI/custom_nodes")
  else
    dirs=("${data_dir}/comfyui/models"
          "${data_dir}/comfyui/output"
          "${data_dir}/comfyui/input"
          "${data_dir}/comfyui/workflows")
  fi

  for d in "${dirs[@]}"; do
    mkdir -p "$d" || { warn "comfyui mkdir failed on ${d} (non-fatal)"; continue; }
    # [NON-FATAL: comfyui] ComfyUI will fail its own healthcheck if ACLs remain broken.
    chmod 2775 "$d" && setfacl -R -d -m "u::rwx,u:$(id -u comfyui 2>>"$LOGFILE" || echo 1000):rwx,g::rwx,o::rx" "$d" \
      || warn "comfyui ACL failed on ${d} (non-fatal)"
  done
}

# Download user-specified ComfyUI models from COMFYUI_EXTRA_MODELS env var
comfyui_preload_models() {
  local ds_dir="$1"
  local gpu_backend="${2:-nvidia}"
  local env_file="${ds_dir}/.env"
  local data_dir="${ds_dir}/data"

  local extra_models
  extra_models="$(env_get "$env_file" "COMFYUI_EXTRA_MODELS")"
  [[ -z "$extra_models" ]] && return 0

  local models_root
  if [[ "$gpu_backend" == "amd" ]]; then
    models_root="${data_dir}/comfyui/ComfyUI/models"
  else
    models_root="${data_dir}/comfyui/models"
  fi
  mkdir -p "$models_root"

  log "Processing ComfyUI extra models..."
  echo "$extra_models" | tr ';' '\n' | while IFS='|' read -r url target; do
    url=$(echo "$url" | xargs)
    target=$(echo "$target" | xargs)
    [[ -z "$url" || -z "$target" ]] && continue
    _download_comfyui_model "$models_root" "$url" "$target"
  done

  apply_data_acl "$models_root"
  log "ComfyUI model preload complete"
}

_download_comfyui_model() {
  local models_root="$1" url="$2" target="$3"
  local dest="${models_root}/${target}"
  local dest_dir
  dest_dir="$(dirname "$dest")"
  mkdir -p "$dest_dir"

  [[ -f "$dest" ]] && { log "  Already exists: ${target}"; return 0; }

  log "  Downloading: ${target}..."
  if command -v aria2c &>/dev/null; then
    # [NON-FATAL: comfyui] Optional extra model download failures should not block install.
    aria2c -x 4 -s 4 -k 5M --file-allocation=none --console-log-level=warn \
      -d "$dest_dir" -o "$(basename "$dest")" "$url" 2>&1 | tail -3 \
      || warn "  Failed to download ${target} (non-fatal)"
  else
    # [NON-FATAL: comfyui] Optional extra model download failures should not block install.
    curl -L --progress-bar -o "$dest" "$url" \
      || warn "  Failed to download ${target} (non-fatal)"
  fi
}

# Patch OpenClaw's inject-token.js for model reference compatibility
patch_openclaw_inject_token_runtime() {
  local ds_dir="$1"
  local target="${ds_dir}/config/openclaw/inject-token.js"

  [[ ! -f "$target" ]] && return 0
  if ! command -v perl &>/dev/null; then
    warn "perl missing — cannot patch OpenClaw injector"
    return 0
  fi

  # Already patched? Keep idempotent.
  if grep -q "const providerMap = config.models?.providers || config.providers || null;" "$target" \
    && grep -q "firstModel.name = LLM_MODEL;" "$target" \
    && grep -q "updated legacy agent model refs ->" "$target"; then
    log "OpenClaw injector patch already present: ${target}"
    return 0
  fi

  local before_hash
  before_hash=$(sha256sum "$target" | awk '{print $1}' || echo "")

  local subs
  subs=$(perl -0777 -i - "$target" <<'PERL'
my $replacement = <<'JS';
  // Fix model references to match what llama-server actually serves
  if (LLM_MODEL) {
    const providerMap = config.models?.providers || config.providers || null;
    const providerName = providerMap ? Object.keys(providerMap)[0] : null;

    if (providerName && providerMap[providerName]) {
      const provider = providerMap[providerName];
      const ollamaUrl = process.env.OLLAMA_URL || '';
      const litellmKey = process.env.LITELLM_KEY || '';
      if (ollamaUrl) {
        const newBase = ollamaUrl.replace(/\/$/, '') + '/v1';
        if (provider.baseUrl !== newBase) {
          console.log(`[inject-token] updated provider baseUrl: ${provider.baseUrl} -> ${newBase}`);
          provider.baseUrl = newBase;
        }
        if (litellmKey && provider.apiKey !== litellmKey) {
          provider.apiKey = litellmKey;
          console.log(`[inject-token] updated provider apiKey from env`);
        }
      }

      if (Array.isArray(provider.models) && provider.models.length > 0) {
        const firstModel = provider.models[0];
        if (firstModel && typeof firstModel === 'object') {
          const oldValue = firstModel.name || firstModel.id || '<unset>';
          if (firstModel.name !== LLM_MODEL || firstModel.id !== LLM_MODEL) {
            firstModel.name = LLM_MODEL;
            firstModel.id = LLM_MODEL;
            console.log(`[inject-token] updated provider model: ${oldValue} -> ${LLM_MODEL}`);
          }
        }
      }
    }

    if (config.agents?.defaults) {
      const d = config.agents.defaults;
      const fullOld = d.model?.primary || '';
      if (fullOld && providerName) {
        const fullNew = `${providerName}/${LLM_MODEL}`;
        if (fullOld !== fullNew) {
          d.model = { primary: fullNew };
          d.models = { [fullNew]: {} };
          if (d.subagents) d.subagents.model = fullNew;
          console.log(`[inject-token] updated agent model refs: ${fullOld} -> ${fullNew}`);
        }
      }
    }

    if (config.agent && providerName) {
      const fullNew = `${providerName}/${LLM_MODEL}`;
      if (config.agent.model !== fullNew) {
        config.agent.model = fullNew;
        if (config.subagent) config.subagent.model = fullNew;
        console.log(`[inject-token] updated legacy agent model refs -> ${fullNew}`);
      }
    }
  }

  // Override LLM baseUrl for Token Spy monitoring (if OPENCLAW_LLM_URL is set)
JS

my $n = s{
\Q  // Fix model references to match what llama-server actually serves
  if (LLM_MODEL) {\E
.*?
\Q  }

  // Override LLM baseUrl for Token Spy monitoring (if OPENCLAW_LLM_URL is set)\E
}{$replacement}sx;

print $n;
PERL
)

  _verify_openclaw_patch "$target" "$before_hash" "${subs:-0}"
}

_verify_openclaw_patch() {
  local target="$1" before_hash="$2" subs="$3"

  if [[ "$subs" -eq 0 ]]; then
    if grep -q "const providerMap = config.models?.providers || config.providers || null;" "$target"; then
      log "OpenClaw injector patch already present: ${target}"
    else
      warn "OpenClaw injector patch pattern not found in ${target} — leaving unchanged"
    fi
    return 0
  fi

  if grep -q "const providerMap = config.models?.providers || config.providers || null;" "$target" \
    && grep -q "firstModel.name = LLM_MODEL;" "$target"; then
    local after_hash
    after_hash=$(sha256sum "$target" | awk '{print $1}' || echo "")
    if [[ "$before_hash" != "$after_hash" ]]; then
      log "Patched OpenClaw injector: ${target}"
    else
      log "OpenClaw injector patch already present: ${target}"
    fi
  else
    warn "OpenClaw injector patch could not be verified: ${target}"
  fi
}
