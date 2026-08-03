#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Phase 06: Bootstrap Model
# ============================================================================
# Part of: ods/installers/p2p-gpu/phases/
# Purpose: Ensure a usable GGUF model file exists so llama-server can start.
#          If the GPU can handle a bigger model, download it in the background
#          and hot-swap once ready (zero downtime).
#
# Expects: ODS_DIR, GPU_BACKEND, GPU_VRAM, GPU_COUNT,
#          log(), warn(), err(), env_get(), env_set(),
#          fix_known_uid_requirements(), apply_data_acl(),
#          check_disk_for_download(), resolve_model_url(),
#          resolve_tier_for_gpu(), _store_pid(), create_model_swap_watcher()
# Provides: Verified GGUF_FILE in .env pointing to a real model;
#           background download of tier model + swap watcher (if bootstrapped)
#
# Fixes covered: #19 (bootstrap model missing), #20 (llama-server hang)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 6/12: Ensuring bootstrap model is available"

# Derive LLM_MODEL identifier from GGUF filename.
# Strips .gguf extension and quantization suffix, lowercases.
# Example: Qwen3-30B-A3B-Q4_K_M.gguf -> qwen3-30b-a3b
_derive_llm_model() {
  echo "$1" \
    | sed -E 's/\.(gguf|GGUF)$//' \
    | sed -E 's/-Q[0-9]+([._][A-Za-z0-9]+)*$//' \
    | tr '[:upper:]' '[:lower:]'
}

env_file="${ODS_DIR}/.env"
data_dir="${ODS_DIR}/data"
models_dir="${data_dir}/models"
mkdir -p "$models_dir"

model_ready=false

# ── Step 1: Resolve the GPU-optimal tier model ────────────────────────────────
# This is the model the GPU *should* run. We determine it from VRAM, not from
# whatever the installer may or may not have written to .env.
resolve_tier_for_gpu "$ODS_DIR" "$GPU_BACKEND" "${GPU_VRAM:-0}" "${GPU_COUNT:-1}"
tier_gguf="${TIER_GGUF_FILE}"
tier_url="${TIER_GGUF_URL}"
tier_size_mb="${TIER_MODEL_SIZE_MB}"

# Persist model size for VRAM budget calculations in later phases
if [[ "${TIER_MODEL_SIZE_MB:-0}" -gt 0 ]]; then
  env_set "$env_file" "LLM_MODEL_SIZE_MB" "$TIER_MODEL_SIZE_MB"
fi

if [[ -n "$tier_gguf" ]]; then
  log "GPU-optimal model for ${GPU_BACKEND} (${GPU_VRAM:-0}MB VRAM): ${tier_gguf} (~${tier_size_mb}MB)"
else
  warn "Could not determine tier model — will use bootstrap model only"
fi

# ── Step 2: Check if we already have a usable model ──────────────────────────

# Check if the tier model itself is already downloaded
if [[ -n "$tier_gguf" && -f "${models_dir}/${tier_gguf}" ]]; then
  file_size=$(stat -c%s "${models_dir}/${tier_gguf}" || echo 0)
  if [[ $file_size -gt 100000000 ]]; then
    env_set "$env_file" "GGUF_FILE" "$tier_gguf"
    env_set "$env_file" "LLM_MODEL" "$(_derive_llm_model "$tier_gguf")"
    model_ready=true
    log "Tier model already present: ${tier_gguf} ($(( file_size / 1048576 )) MB)"
  else
    warn "Tier model exists but too small (${file_size} bytes) — likely corrupt"
    rm -f "${models_dir}/${tier_gguf}"
  fi
fi

# Check configured GGUF_FILE from .env
if [[ "$model_ready" != "true" ]]; then
  gguf_file=$(env_get "$env_file" "GGUF_FILE")
  if [[ -n "$gguf_file" && -f "${models_dir}/${gguf_file}" ]]; then
    file_size=$(stat -c%s "${models_dir}/${gguf_file}" || echo 0)
    if [[ $file_size -gt 100000000 ]]; then
      model_ready=true
      log "Model verified: ${gguf_file} ($(( file_size / 1048576 )) MB)"
      if [[ -z "$(env_get "$env_file" "LLM_MODEL")" ]]; then
        env_set "$env_file" "LLM_MODEL" "$(_derive_llm_model "$gguf_file")"
      fi
    else
      warn "Model file exists but too small (${file_size} bytes) — likely corrupt"
      rm -f "${models_dir}/${gguf_file}"
    fi
  fi
fi

# Check for ANY .gguf file as fallback
if [[ "$model_ready" != "true" ]]; then
  any_model=$(find "$models_dir" -name "*.gguf" -size +100M 2>>"$LOGFILE" | head -1 || echo "")
  if [[ -n "$any_model" ]]; then
    found_name=$(basename "$any_model")
    env_set "$env_file" "GGUF_FILE" "$found_name"
    env_set "$env_file" "LLM_MODEL" "$(_derive_llm_model "$found_name")"
    model_ready=true
    log "Found existing model: ${found_name} — updated GGUF_FILE"
  fi
fi

# ── Step 3: Download bootstrap model if nothing usable exists ─────────────────
if [[ "$model_ready" != "true" ]]; then
  # [FIX: disk-check] Verify disk space before downloading
  if ! check_disk_for_download "$models_dir" 2; then
    err "Cannot download bootstrap model — insufficient disk space"
    warn "Continuing without a model — llama-server will not start"
  else
    if [[ "${TLS_OK:-true}" != "true" ]]; then
      warn "Skipping bootstrap download because TLS trust is broken (TLS_OK=false)"
      warn "Fix TLS trust (proxy root CA) and re-run setup to download models"
    else
      warn "No usable model found — downloading bootstrap model..."
      bootstrap_url="https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_K_M.gguf"
      bootstrap_name="Qwen3-0.6B-Q4_K_M.gguf"

      if command -v aria2c &>/dev/null; then
        set +e
        aria2c -x 8 -s 8 -k 5M --file-allocation=none --console-log-level=notice \
          --check-integrity=true \
          -d "$models_dir" -o "$bootstrap_name" "$bootstrap_url" 2>&1 | tail -5
        dl_rc=${PIPESTATUS[0]}
        set -e
        if [[ "$dl_rc" -ne 0 ]]; then
          warn "Bootstrap download failed (aria2c exit ${dl_rc}) — check TLS/proxy CA"
        fi
      else
        set +e
        curl -L --fail --progress-bar -o "${models_dir}/${bootstrap_name}" "$bootstrap_url"
        dl_rc=$?
        set -e
        if [[ "$dl_rc" -ne 0 ]]; then
          warn "Bootstrap download failed (curl exit ${dl_rc}) — check TLS/proxy CA"
        fi
      fi

      # [FIX: bootstrap-size] Validate downloaded file size (>50MB for smallest GGUF)
      if [[ -f "${models_dir}/${bootstrap_name}" ]]; then
        dl_size=$(stat -c%s "${models_dir}/${bootstrap_name}" || echo 0)
        if [[ "$dl_size" -gt 50000000 ]]; then
          env_set "$env_file" "GGUF_FILE" "$bootstrap_name"
          env_set "$env_file" "LLM_MODEL" "$(_derive_llm_model "$bootstrap_name")"
          model_ready=true
          log "Bootstrap model downloaded: ${bootstrap_name} ($(( dl_size / 1048576 )) MB)"
        else
          err "Downloaded model too small (${dl_size} bytes) — likely incomplete or corrupt"
          rm -f "${models_dir}/${bootstrap_name}"
          warn "Continuing without a model — llama-server will not start"
        fi
      else
        err "Failed to download bootstrap model — llama-server will not start"
        warn "Continuing anyway — other services may still work"
      fi
    fi
  fi
fi

# ── Step 4: Queue background download of tier model if needed ─────────────────
# If we're running a smaller model than what the GPU can handle, download the
# tier model in the background. The swap watcher will hot-swap GGUF_FILE and
# recreate llama-server via `docker compose up -d` once the download completes.
current_gguf=$(env_get "$env_file" "GGUF_FILE")
if [[ "${TLS_OK:-true}" != "true" ]]; then
  warn "Skipping tier model download because TLS trust is broken (TLS_OK=false)"
elif [[ -n "$tier_gguf" && "$tier_gguf" != "${current_gguf:-}" ]]; then
  # Determine disk space needed (model size in MB → GB, rounded up + 2GB buffer)
  needed_gb=$(( (tier_size_mb / 1024) + 2 ))
  [[ $needed_gb -lt 5 ]] && needed_gb=5

  if check_disk_for_download "$models_dir" "$needed_gb"; then
    # Resolve URL: prefer TIER_GGUF_URL from tier resolution, fallback to resolve_model_url
    if [[ -z "$tier_url" ]]; then
      tier_url=$(resolve_model_url "$ODS_DIR" "$tier_gguf") || tier_url=""
    fi

    if [[ -n "$tier_url" ]]; then
      log "Queuing background download: ${tier_gguf} (~${tier_size_mb}MB)"
      log "  URL: ${tier_url}"
      log "  Current model: ${current_gguf:-none}"
      log "  Once complete, llama-server will auto-swap to the bigger model"
      mkdir -p "${ODS_DIR}/logs"

      if command -v aria2c &>/dev/null; then
        nohup aria2c \
          -x 8 -s 8 -k 10M \
          --continue=true \
          --max-tries=0 \
          --retry-wait=5 \
          --timeout=60 \
          --connect-timeout=30 \
          --file-allocation=none \
          --auto-file-renaming=false \
          --console-log-level=warn \
          --summary-interval=30 \
          --check-integrity=true \
          -d "$models_dir" \
          -o "$tier_gguf" \
          "$tier_url" \
          >> "${ODS_DIR}/logs/aria2c-download.log" 2>&1 &
      else
        nohup curl -L --fail -o "${models_dir}/${tier_gguf}" "$tier_url" \
          >> "${ODS_DIR}/logs/aria2c-download.log" 2>&1 &
      fi

      dl_pid=$!
      _store_pid "aria2c-model" "$dl_pid"
      log "Background download started (PID: ${dl_pid})"
      create_model_swap_watcher "$ODS_DIR" "$tier_gguf"
    else
      warn "Could not resolve download URL for ${tier_gguf} — staying on ${current_gguf:-bootstrap model}"
    fi
  else
    warn "Insufficient disk for tier model (~${tier_size_mb}MB) — staying on ${current_gguf:-bootstrap model}"
  fi
elif [[ -n "$tier_gguf" && "$tier_gguf" == "${current_gguf:-}" ]]; then
  log "Already running the GPU-optimal model: ${tier_gguf}"
fi

fix_known_uid_requirements "$data_dir" "$GPU_BACKEND"
apply_data_acl "$models_dir"

# Re-run VRAM context cap now that we know the actual model size
_cap_context_for_vram "$ODS_DIR"
