#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Model Management
# ============================================================================
# Part of: ods/installers/p2p-gpu/lib/
# Purpose: Model URL resolution, aria2c-optimized downloads, model swap
#          watcher for background upgrades, disk-space gating
#
# Expects: LOGFILE, PIDFILE_DIR, log(), warn(), env_get(), env_set()
# Provides: resolve_model_url(), optimize_model_download(),
#           create_model_swap_watcher(), check_disk_for_download()
#
# Modder notes:
#   resolve_model_url tries 4 strategies in priority order:
#     1. model-upgrade log  2. upstream tier-map.sh
#     3. backend JSON configs  4. HuggingFace org probing
#   create_model_swap_watcher generates a self-contained script that polls
#   for aria2c completion and hot-swaps the active model.
#   PIDs are tracked in PIDFILE_DIR for safe cleanup (no pkill -f).
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

# ── GPU-aware tier model resolution ───────────────────────────────────────────
# Maps GPU VRAM (MB) to the optimal tier model. Mirrors the upstream tier-map
# logic from ods/installers/lib/tier-map.sh but is self-contained so
# p2p-gpu stays isolated from the core codebase.
#
# If the upstream tier-map.sh exists (after ODS is cloned), we source it
# directly for accuracy. Otherwise, fall back to a built-in VRAM lookup table.
#
# Sets: TIER_GGUF_FILE, TIER_GGUF_URL, TIER_MODEL_SIZE_MB
# Args: $1 = ds_dir, $2 = gpu_backend, $3 = gpu_vram_mb, $4 = gpu_count
resolve_tier_for_gpu() {
  local ds_dir="$1" gpu_backend="$2" vram_mb="${3:-0}" gpu_count="${4:-1}"
  local tier_map="${ds_dir}/installers/lib/tier-map.sh"

  local total_vram_mb="${GPU_TOTAL_VRAM:-$(( vram_mb * gpu_count ))}"
  local reserve_mb_per_gpu="${P2P_TIER_VRAM_RESERVE_MB:-1024}"
  local effective_vram_mb="$vram_mb"
  if [[ "$gpu_count" -ge 2 ]]; then
    local reserve_total=$(( reserve_mb_per_gpu * gpu_count ))
    if [[ "$total_vram_mb" -gt "$reserve_total" ]]; then
      effective_vram_mb=$(( total_vram_mb - reserve_total ))
    else
      effective_vram_mb="$total_vram_mb"
    fi
    log "Tier VRAM budget: per_gpu=${vram_mb}MB total=${total_vram_mb}MB reserve=${reserve_mb_per_gpu}MB x${gpu_count} -> effective=${effective_vram_mb}MB"
  fi

  TIER_GGUF_FILE=""
  TIER_GGUF_URL=""
  TIER_MODEL_SIZE_MB=0

  # Strategy 1: Use upstream tier-map.sh if available (most accurate)
  if [[ -f "$tier_map" ]]; then
    local tier=""
    if [[ "$gpu_backend" == "nvidia" ]]; then
      if [[ $effective_vram_mb -ge 90000 ]]; then tier="NV_ULTRA"
      elif [[ $effective_vram_mb -ge 40000 ]]; then tier=4
      elif [[ $effective_vram_mb -ge 20000 ]]; then tier=3
      elif [[ $effective_vram_mb -ge 12000 ]]; then tier=2
      elif [[ $effective_vram_mb -lt 4000 ]]; then tier=0
      else tier=1; fi
    elif [[ "$gpu_backend" == "amd" ]]; then
      if [[ $effective_vram_mb -ge 20000 ]]; then tier=3
      elif [[ $effective_vram_mb -ge 12000 ]]; then tier=2
      else tier=1; fi
    else
      tier=0  # CPU-only
    fi

    # Source upstream tier-map in a subshell to avoid polluting our namespace
    local result
    result=$(
      TIER="$tier"
      MODEL_PROFILE="${MODEL_PROFILE:-qwen}"
      error() { echo "ERROR: $*" >&2; return 1; }
      source "$tier_map" 2>>"$LOGFILE"
      resolve_tier_config 2>>"$LOGFILE"
      echo "${GGUF_FILE}|${GGUF_URL:-}|${LLM_MODEL_SIZE_MB:-0}"
    ) || result=""

    if [[ -n "$result" ]]; then
      TIER_GGUF_FILE="${result%%|*}"
      local rest="${result#*|}"
      TIER_GGUF_URL="${rest%%|*}"
      TIER_MODEL_SIZE_MB="${rest##*|}"
      if [[ -n "$TIER_GGUF_FILE" ]]; then
        log "Tier resolved via upstream tier-map: ${TIER_GGUF_FILE} (tier ${tier}, ${effective_vram_mb}MB effective VRAM)"
        return 0
      fi
    fi
  fi

  # Strategy 2: Built-in VRAM lookup (fallback when tier-map.sh unavailable)
  # Uses qwen profile defaults matching upstream's set_qwen_tier_config()
  if [[ "$gpu_backend" == "nvidia" || "$gpu_backend" == "amd" ]]; then
    local effective_vram="$effective_vram_mb"

    if [[ $effective_vram -ge 90000 ]]; then
      # NV_ULTRA: B200 (180GB), multi-A100/H100, etc.
      TIER_GGUF_FILE="qwen3-coder-next-Q4_K_M.gguf"
      TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf"
      TIER_MODEL_SIZE_MB=48500
    elif [[ $effective_vram -ge 24000 ]]; then
      # Tier 3-4: RTX 3090/4090 (24GB), A6000 (48GB), A100 (40/80GB), H100 (80GB)
      TIER_GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
      TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
      TIER_MODEL_SIZE_MB=18600
    elif [[ $effective_vram -ge 12000 ]]; then
      # Tier 2: RTX 3060 (12GB), RTX 4070 (12GB), RTX 3080 Ti (12GB)
      TIER_GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
      TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
      TIER_MODEL_SIZE_MB=5760
    elif [[ $effective_vram -ge 4000 ]]; then
      # Tier 1: RTX 3070 (8GB), RTX 3080 (10GB), GPUs with 4-12GB VRAM
      # 4B model (2,870 MB) leaves enough headroom for KV cache on 8GB GPUs
      TIER_GGUF_FILE="Qwen3.5-4B-Q4_K_M.gguf"
      TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"
      TIER_MODEL_SIZE_MB=2870
    else
      # Tier 0: <4GB VRAM or CPU-only
      TIER_GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
      TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
      TIER_MODEL_SIZE_MB=1500
    fi
  else
    TIER_GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
    TIER_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
    TIER_MODEL_SIZE_MB=1500
  fi

  log "Tier resolved via built-in lookup: ${TIER_GGUF_FILE} (${effective_vram_mb}MB effective VRAM)"
}

# ── [FIX: disk-check] Verify sufficient disk before starting a download ─────
# Returns 0 if enough space, 1 if insufficient.
# Args: $1 = directory to check, $2 = minimum GB required (default: 5)
check_disk_for_download() {
  local target_dir="$1"
  local min_gb="${2:-5}"
  local avail_gb
  avail_gb=$(df -BG --output=avail "$target_dir" 2>>"$LOGFILE" | tail -1 | tr -dc '0-9')
  if [[ "${avail_gb:-0}" -lt "$min_gb" ]]; then
    warn "Insufficient disk space: ${avail_gb}GB available, ${min_gb}GB needed in ${target_dir}"
    return 1
  fi
  return 0
}

# ── [FIX: pkill] PID-file based process management ─────────────────────────
# Store a background process PID so we can stop it safely later.
_store_pid() {
  local name="$1" pid="$2"
  # [NON-FATAL: pidfile] Missing pidfile dir only affects cleanup tracking.
  mkdir -p "$PIDFILE_DIR" 2>>"$LOGFILE" || warn "could not create pidfile directory ${PIDFILE_DIR} (non-fatal)"
  echo "$pid" > "${PIDFILE_DIR}/${name}.pid"
}

# Kill a previously stored PID by name. Safe — only kills the exact PID.
_kill_stored_pid() {
  local name="$1"
  local pidfile="${PIDFILE_DIR}/${name}.pid"
  [[ ! -f "$pidfile" ]] && return 0
  local pid
  pid=$(cat "$pidfile" 2>>"$LOGFILE" || echo "")
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then  # stderr expected: process may already have exited
    # [NON-FATAL: cleanup] Process may already be gone; continue cleanup.
    kill "$pid" 2>>"$LOGFILE" || warn "Could not kill ${name} (PID ${pid})"
  fi
  rm -f "$pidfile"
}

# Check if a stored PID is still running.
_is_pid_running() {
  local name="$1"
  local pidfile="${PIDFILE_DIR}/${name}.pid"
  [[ ! -f "$pidfile" ]] && return 1
  local pid
  pid=$(cat "$pidfile" 2>>"$LOGFILE" || echo "")
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null # stderr expected: process may already have exited
}

# Resolve download URL for a model filename
resolve_model_url() {
  local ds_dir="$1" model_name="$2"

  # Strategy 1: model-upgrade log
  local url
  url=$(_resolve_from_log "$ds_dir" "$model_name") && [[ -n "$url" ]] && { echo "$url"; return 0; }

  # Strategy 2: upstream tier-map.sh
  url=$(_resolve_from_tiermap "$ds_dir" "$model_name") && [[ -n "$url" ]] && { echo "$url"; return 0; }

  # Strategy 3: backend JSON configs
  url=$(_resolve_from_backends "$ds_dir" "$model_name") && [[ -n "$url" ]] && { echo "$url"; return 0; }

  # Strategy 4: probe common HuggingFace orgs
  url=$(_resolve_from_hf_probe "$model_name") && [[ -n "$url" ]] && { echo "$url"; return 0; }

  return 1
}

_resolve_from_log() {
  local ds_dir="$1" model_name="$2"
  local upgrade_log="${ds_dir}/logs/model-upgrade.log"
  [[ ! -f "$upgrade_log" ]] && return 1
  grep -oP 'https://huggingface\.co/[^\s"]+'"${model_name}" "$upgrade_log" | tail -1 || return 1
}

_resolve_from_tiermap() {
  local ds_dir="$1" model_name="$2"
  local tier_map="${ds_dir}/installers/lib/tier-map.sh"
  [[ ! -f "$tier_map" ]] && return 1
  grep -oP 'https://huggingface\.co/[^\s"'"'"']+'"${model_name}" "$tier_map" | head -1 || return 1
}

_resolve_from_backends() {
  local ds_dir="$1" model_name="$2"
  local backend_dir="${ds_dir}/config/backends"
  [[ ! -d "$backend_dir" ]] && return 1
  grep -rhoP 'https://huggingface\.co/[^\s"]+'"${model_name}" "$backend_dir" | head -1 || return 1
}

_resolve_from_hf_probe() {
  local model_name="$1"
  local base_name
  base_name=$(echo "$model_name" | sed -E 's/-[QqFf][0-9_]+[A-Za-z]*\.gguf$//')
  [[ -z "$base_name" ]] && return 1

  local org
  for org in "unsloth" "bartowski" "lmstudio-community"; do
    local test_url="https://huggingface.co/${org}/${base_name}-GGUF/resolve/main/${model_name}"
    if curl -sfI --max-time 10 "$test_url" | grep -qi "200\|302\|301"; then
      echo "$test_url"
      return 0
    fi
  done
  return 1
}

# Resume/restart incomplete model downloads with aria2c
optimize_model_download() {
  local ds_dir="$1"
  local data_dir="${ds_dir}/data"

  local part_files
  part_files=$(find "${data_dir}/models/" -name "*.gguf.part" -type f 2>&1 || echo "")

  if [[ -z "$part_files" ]]; then
    if _is_pid_running "aria2c-model"; then
      log "aria2c download already running"
      return 0
    fi
    log "No incomplete model downloads found — models are ready"
    return 0
  fi

  local part_file part_name part_size_mb gguf_url
  part_file=$(echo "$part_files" | head -1)
  part_name=$(basename "$part_file" .part)
  part_size_mb=$(( $(stat -c%s "$part_file" || echo 0) / 1048576 ))

  warn "Incomplete download: ${part_name} (${part_size_mb} MB so far)"

  # [FIX: pkill] Kill only known PIDs, not by pattern
  _kill_stored_pid "curl-model"
  _kill_stored_pid "wget-model"
  sleep 2

  # [FIX: disk-check] Verify at least 5GB free before resuming
  if ! check_disk_for_download "${data_dir}/models" 5; then
    warn "Skipping model download — insufficient disk space"
    return 0
  fi

  gguf_url=$(resolve_model_url "$ds_dir" "$part_name") || {
    warn "Could not resolve download URL for ${part_name} — leaving original download"
    return 0
  }

  log "Restarting download with aria2c (8 threads)..."
  rm -f "$part_file"
  mkdir -p "${ds_dir}/logs"

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
    -d "${data_dir}/models" \
    -o "${part_name}" \
    "${gguf_url}" \
    >> "${ds_dir}/logs/aria2c-download.log" 2>&1 &

  local aria_pid=$!
  _store_pid "aria2c-model" "$aria_pid"
  log "aria2c started (PID: ${aria_pid})"
  create_model_swap_watcher "$ds_dir" "$part_name"
}

# Generate and start a model swap watcher script
create_model_swap_watcher() {
  local ds_dir="$1" model_name="$2"
  local watcher_script="${ds_dir}/scripts/model-swap-on-complete.sh"
  local pidfile_dir="${PIDFILE_DIR:-/var/run/ods-p2p-gpu}"
  mkdir -p "${ds_dir}/scripts"

  cat > "$watcher_script" << 'WATCHER_EOF'
#!/usr/bin/env bash
set -euo pipefail
# Auto-swap model when aria2c download completes

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="${SCRIPT_DIR}/data/models"
ENV_FILE="${SCRIPT_DIR}/.env"
PIDFILE="__PIDFILE_DIR__/aria2c-model.pid"
TARGET_MODEL="__TARGET_MODEL__"
warn() { echo -e "\033[1;33m[!]\033[0m $*" >&2; }

compose_cmd() {
  if docker compose version &>/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose &>/dev/null; then
    echo "docker-compose"
  else
    echo "docker restart"
  fi
}

is_download_running() {
  [[ ! -f "$PIDFILE" ]] && return 1
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null || echo "") # stderr expected: pidfile can be unreadable/missing during shutdown race
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null # stderr expected: "No such process" while download exits
}

swap_model() {
  local new_model="$1"
  local old_model
  old_model=$(grep '^GGUF_FILE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || echo "")
  [[ "$new_model" == "$old_model" ]] && return 0

  # Convert GGUF filename -> Dream model id used by other services.
  # Example: Qwen3-30B-A3B-Q4_K_M.gguf -> qwen3-30b-a3b
  local new_llm_model
  new_llm_model=$(echo "$new_model" \
    | sed -E 's/\.(gguf|GGUF)$//' \
    | sed -E 's/-Q[0-9]+([._][A-Za-z0-9]+)*$//' \
    | tr '[:upper:]' '[:lower:]')

  # Validate new model file before swapping
  local model_path="${MODEL_DIR}/${new_model}"
  if [[ ! -f "$model_path" ]]; then
    warn "Model file not found: ${model_path} — skipping swap"
    return 1
  fi
  local file_size
  file_size=$(stat -c%s "$model_path" 2>/dev/null || echo 0) # stderr expected: file can disappear during concurrent cleanup
  if [[ "$file_size" -lt 100000000 ]]; then
    warn "Model file too small (${file_size} bytes) — skipping swap"
    return 1
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Swapping: ${old_model} -> ${new_model} ($(( file_size / 1048576 )) MB)"
  # [FIX: tmpfile-race] Use sed -i to avoid world-readable temp file with secrets
  sed -i "s|^GGUF_FILE=.*|GGUF_FILE=${new_model}|" "$ENV_FILE"
  if grep -q '^LLM_MODEL=' "$ENV_FILE"; then
    sed -i "s|^LLM_MODEL=.*|LLM_MODEL=${new_llm_model}|" "$ENV_FILE"
  else
    echo "LLM_MODEL=${new_llm_model}" >> "$ENV_FILE"
  fi

  # Update model size for VRAM budget calculations
  local new_size_mb
  new_size_mb=$(stat -c%s "$model_path" 2>/dev/null || echo 0) # stderr expected: file can disappear during cleanup
  new_size_mb=$(( new_size_mb / 1048576 ))
  sed -i "s|^LLM_MODEL_SIZE_MB=.*|LLM_MODEL_SIZE_MB=${new_size_mb}|" "$ENV_FILE"
  if ! grep -q '^LLM_MODEL_SIZE_MB=' "$ENV_FILE"; then
    echo "LLM_MODEL_SIZE_MB=${new_size_mb}" >> "$ENV_FILE"
  fi

  # Use compose recreate (re-reads .env) instead of docker restart (ignores .env changes)
  local cmd
  cmd=$(compose_cmd)
  if [[ "$cmd" == "docker compose" ]]; then
    # [NON-FATAL: service] Llama restart can be retried if compose fails.
    cd "$SCRIPT_DIR" && docker compose up -d llama-server || warn "compose recreate failed (non-fatal)"
  elif [[ "$cmd" == "docker-compose" ]]; then
    # [NON-FATAL: service] Llama restart can be retried if compose fails.
    cd "$SCRIPT_DIR" && docker-compose up -d llama-server || warn "compose recreate failed (non-fatal)"
  else
    # [NON-FATAL: service] Restart failure should not block the watcher.
    docker restart ods-llama-server || warn "llama-server restart failed (non-fatal)"
  fi
  # Restart dependent services so they pick up new model env / auto-detection.
  for cname in ods-forge ods-openclaw ods-dashboard-api; do
    if docker ps --format '{{.Names}}' | grep -qx "$cname"; then
      # [NON-FATAL: service] Dependent restarts are best-effort.
      docker restart "$cname" || warn "${cname} restart failed (non-fatal)"
    fi
  done
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Swapped to ${new_model} — llama-server reloading"
}

while true; do
  if ! is_download_running; then
    if [[ -n "${TARGET_MODEL:-}" && -f "${MODEL_DIR}/${TARGET_MODEL}" ]]; then
      swap_model "$TARGET_MODEL"
    else
      local_model=$(ls -S "${MODEL_DIR}"/*.gguf 2>&1 | head -1 | xargs -r basename || echo "")
      if [[ -n "${local_model:-}" ]]; then
        swap_model "$local_model"
      fi
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watcher exiting — download complete"
    exit 0
  fi
  sleep 30
done
WATCHER_EOF

  sed -i "s|__PIDFILE_DIR__|${pidfile_dir}|g" "$watcher_script"
  sed -i "s|__TARGET_MODEL__|${model_name}|g" "$watcher_script"
  chmod +x "$watcher_script"
  nohup "$watcher_script" >> "${ds_dir}/logs/model-swap.log" 2>&1 &
  local watcher_pid=$!
  _store_pid "model-swap-watcher" "$watcher_pid"
  log "Model swap watcher started (PID: ${watcher_pid})"
}
