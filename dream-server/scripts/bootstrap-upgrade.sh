#!/bin/bash
# ============================================================================
# bootstrap-upgrade.sh — Background Model Download + Auto Hot-Swap
# ============================================================================
# Runs in the background after the installer starts services with the
# bootstrap model. Downloads the full tier-appropriate model, then swaps
# llama-server to the new model with minimal downtime.
#
# Usage (called by phase 11, not directly by users):
#   nohup bash bootstrap-upgrade.sh \
#       <install_dir> <gguf_file> <gguf_url> <gguf_sha256> \
#       <llm_model> <max_context> [<bootstrap_gguf_file>] \
#       > logs/model-upgrade.log 2>&1 &
#
# Arg 7 (bootstrap_gguf_file) is optional and defaults to the historical
# Qwen3.5-2B-Q4_K_M.gguf for backwards compatibility. Phase 11 must pass the
# canonical $BOOTSTRAP_GGUF_FILE from installers/lib/bootstrap-model.sh so the
# Phase 4b cleanup step removes the actual bootstrap model after hot-swap.
#
# On failure: logs the error and exits. The bootstrap model continues
# running — the user can retry via re-running the installer.
# ============================================================================

set -uo pipefail
# Note: no set -e — we handle errors explicitly to avoid killing the
# background process on transient failures.

# ── Arguments ──
INSTALL_DIR="$1"
FULL_GGUF_FILE="$2"
FULL_GGUF_URL="$3"
FULL_GGUF_SHA256="$4"
FULL_LLM_MODEL="$5"
FULL_MAX_CONTEXT="$6"
BOOTSTRAP_GGUF_FILE="${7:-Qwen3.5-2B-Q4_K_M.gguf}"

MODELS_DIR="$INSTALL_DIR/data/models"
ENV_FILE="$INSTALL_DIR/.env"
MODELS_INI="$INSTALL_DIR/config/llama-server/models.ini"
LOG_TAG="[BOOTSTRAP-UPGRADE]"

log()  { echo "$LOG_TAG $(date '+%H:%M:%S') $*"; }
fail() { log "ERROR: $*"; exit 1; }

STATUS_FILE="$INSTALL_DIR/data/bootstrap-status.json"

# Cross-platform file size (GNU stat on Linux/WSL2, BSD stat on macOS)
# IMPORTANT: Try GNU stat -c %s FIRST (Linux). stat -f on Linux returns filesystem
# block count (not file size). BSD stat -f %z is the macOS fallback.
file_size() {
    if stat -c %s "$1" 2>/dev/null; then
        return
    fi
    stat -f %z "$1" 2>/dev/null || echo 0
}

# Get total size via HTTP HEAD request
get_remote_size() {
    local url="$1"
    curl -sI -L --connect-timeout 10 "$url" 2>/dev/null \
        | grep -i '^content-length:' | tail -1 | tr -dc '0-9'
}

# Write status JSON (atomic via mv)
write_status() {
    local status="$1" percent="${2:-}" downloaded="${3:-0}" total="${4:-0}" speed="${5:-0}" eta="${6:-}"
    local _safe_model="${FULL_GGUF_FILE//\"/\\\"}"
    cat > "$STATUS_FILE.tmp" << STATUSEOF
{
  "status": "$status",
  "model": "$_safe_model",
  "percent": ${percent:-null},
  "bytesDownloaded": $downloaded,
  "bytesTotal": $total,
  "speedBytesPerSec": $speed,
  "eta": "${eta:-}",
  "updatedAt": "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date '+%Y-%m-%dT%H:%M:%SZ')"
}
STATUSEOF
    mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}

sync_windows_opencode_config() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) ;;
        *) return 0 ;;
    esac

    local sync_script="$INSTALL_DIR/scripts/update-windows-opencode-config.ps1"
    [[ -f "$sync_script" ]] || return 0

    local ps_cmd=""
    if command -v powershell.exe >/dev/null 2>&1; then
        ps_cmd="powershell.exe"
    elif command -v pwsh.exe >/dev/null 2>&1; then
        ps_cmd="pwsh.exe"
    fi
    [[ -n "$ps_cmd" ]] || return 0

    local install_dir_arg="$INSTALL_DIR"
    local sync_script_arg="$sync_script"
    if command -v cygpath >/dev/null 2>&1; then
        install_dir_arg=$(cygpath -w "$INSTALL_DIR")
        sync_script_arg=$(cygpath -w "$sync_script")
    fi

    log "Refreshing Windows OpenCode config for model: $FULL_GGUF_FILE"
    "$ps_cmd" -NoProfile -ExecutionPolicy Bypass -File "$sync_script_arg" -InstallDir "$install_dir_arg" \
        >/dev/null 2>&1 || log "WARNING: OpenCode config refresh failed (non-fatal)"
}

# Background monitor: polls .part file size every 2s
monitor_download() {
    local part_file="$1" total_bytes="$2"

    # Wait for curl to create the .part file (up to 30s)
    for _wait in $(seq 1 30); do
        [[ -f "$part_file" ]] && break
        sleep 1
    done
    [[ -f "$part_file" ]] || return 0

    local prev_bytes=0 prev_time
    prev_time=$(date +%s)

    while [[ -f "$part_file" ]]; do
        sleep 2
        [[ -f "$part_file" ]] || break

        local current_bytes
        current_bytes=$(file_size "$part_file")
        local now
        now=$(date +%s)
        local elapsed=$((now - prev_time))

        local speed=0
        if [[ $elapsed -gt 0 && $current_bytes -ge $prev_bytes ]]; then
            speed=$(( (current_bytes - prev_bytes) / elapsed ))
        fi

        local percent="null"
        local eta=""
        if [[ $total_bytes -gt 0 ]]; then
            percent=$(awk "BEGIN { printf \"%.1f\", ($current_bytes / $total_bytes) * 100 }")
            if [[ $speed -gt 0 ]]; then
                local remaining=$(( total_bytes - current_bytes ))
                local eta_secs=$(( remaining / speed ))
                local eta_min=$(( eta_secs / 60 ))
                local eta_sec=$(( eta_secs % 60 ))
                eta="${eta_min}m ${eta_sec}s"
            else
                eta="calculating..."
            fi
        fi

        write_status "downloading" "$percent" "$current_bytes" "$total_bytes" "$speed" "$eta"
        prev_bytes=$current_bytes
        prev_time=$now
    done
}

# ── Docker permission detection ──
# This script runs detached via nohup, so DOCKER_CMD from the parent installer
# is not inherited. For Linux installs we MUST be able to talk to the docker
# daemon — silently failing here leaves the user running the small bootstrap
# model forever. macOS installs use a native llama-server PID file and never
# enter the docker hot-swap path; skip detection there. Mirrors the
# sudo-fallback pattern in installers/phases/05-docker.sh.
DOCKER_CMD=""
DOCKER_COMPOSE_CMD=""
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_CMD="docker"
    elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
        DOCKER_CMD="sudo docker"
        log "Detected docker requires sudo (user not in docker group). Using 'sudo docker'."
    elif [[ ! -f "$INSTALL_DIR/data/.llama-server.pid" ]]; then
        # Linux install: docker is the only hot-swap path. Failing silently
        # would leave the bootstrap model running forever — fail loudly.
        log "ERROR: docker is installed but not accessible by this user."
        log "       Tried 'docker info' and 'sudo -n docker info' — both failed."
        log "       The bootstrap model will continue running. Fix one of:"
        log "         1. Re-login (so 'docker' group membership takes effect), then re-run this script."
        log "         2. Configure passwordless sudo for 'docker' (e.g. NOPASSWD in /etc/sudoers.d)."
        write_status "failed"
        exit 1
    fi

    if [[ -n "$DOCKER_CMD" ]]; then
        # Pick docker compose v2 (plugin) if available, else legacy docker-compose v1.
        if $DOCKER_CMD compose version >/dev/null 2>&1; then
            DOCKER_COMPOSE_CMD="$DOCKER_CMD compose"
        elif command -v docker-compose >/dev/null 2>&1; then
            if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
                DOCKER_COMPOSE_CMD="sudo docker-compose"
            else
                DOCKER_COMPOSE_CMD="docker-compose"
            fi
        fi
    fi
fi

log "Starting full model download: $FULL_GGUF_FILE"
log "URL: $FULL_GGUF_URL"
log "Target: $MODELS_DIR/$FULL_GGUF_FILE"

# ── Phase 1: Download the full model ──
mkdir -p "$MODELS_DIR"

# Get total file size for progress calculation
TOTAL_BYTES=$(get_remote_size "$FULL_GGUF_URL")
[[ -z "$TOTAL_BYTES" ]] && TOTAL_BYTES=0
log "Expected file size: $TOTAL_BYTES bytes"

# Write initial status
write_status "starting" "" 0 "$TOTAL_BYTES" 0 "calculating..."

if [[ -f "$MODELS_DIR/$FULL_GGUF_FILE" ]]; then
    log "Full model already exists on disk, skipping download"
    write_status "complete"
else
    # Start background download monitor
    monitor_download "$MODELS_DIR/$FULL_GGUF_FILE.part" "$TOTAL_BYTES" &
    _monitor_pid=$!
    trap 'kill $_monitor_pid 2>/dev/null || true; write_status "failed"; exit 1' EXIT TERM INT

    # Download with resume support, retry up to 3 times
    _dl_success=false
    for _attempt in 1 2 3; do
        [[ $_attempt -gt 1 ]] && log "Retry attempt $_attempt of 3..." && sleep 5
        if curl -fSL -C - --connect-timeout 30 --max-time 3600 \
                -o "$MODELS_DIR/$FULL_GGUF_FILE.part" "$FULL_GGUF_URL" 2>&1; then
            mv "$MODELS_DIR/$FULL_GGUF_FILE.part" "$MODELS_DIR/$FULL_GGUF_FILE"
            _dl_success=true
            break
        fi
        log "Download attempt $_attempt failed"
    done

    # Stop background monitor
    kill $_monitor_pid 2>/dev/null || true
    trap - EXIT TERM INT

    if [[ "$_dl_success" != "true" ]]; then
        rm -f "$MODELS_DIR/$FULL_GGUF_FILE.part"
        write_status "failed"
        fail "Download failed after 3 attempts. Bootstrap model will continue running."
    fi

    write_status "complete"
    log "Download complete: $FULL_GGUF_FILE"
fi

# ── Phase 2: Verify integrity (if SHA256 provided) ──
if [[ -n "$FULL_GGUF_SHA256" ]]; then
    write_status "verifying" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
    log "Verifying SHA256..."
    if command -v sha256sum &>/dev/null; then
        ACTUAL_HASH=$(sha256sum "$MODELS_DIR/$FULL_GGUF_FILE" 2>/dev/null | awk '{print $1}')
    elif command -v shasum &>/dev/null; then
        ACTUAL_HASH=$(shasum -a 256 "$MODELS_DIR/$FULL_GGUF_FILE" 2>/dev/null | awk '{print $1}')
    else
        log "WARNING: No checksum tool available — skipping SHA256 verification"
        ACTUAL_HASH=""
    fi
    if [[ -n "$ACTUAL_HASH" ]]; then
        if [[ "$ACTUAL_HASH" != "$FULL_GGUF_SHA256" ]]; then
            rm -f "$MODELS_DIR/$FULL_GGUF_FILE"
            write_status "failed"
            fail "SHA256 mismatch (expected: $FULL_GGUF_SHA256, got: $ACTUAL_HASH). Deleted corrupt file."
        fi
        log "SHA256 verified"
    fi
    write_status "complete"
fi

# ── Phase 3: Update .env ──
log "Updating .env..."
if [[ -f "$ENV_FILE" ]]; then
    # Update GGUF_FILE
    if grep -q '^GGUF_FILE=' "$ENV_FILE"; then
        awk -v v="$FULL_GGUF_FILE" '{ if (index($0, "GGUF_FILE=") == 1) print "GGUF_FILE=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    # Update LLM_MODEL
    if grep -q '^LLM_MODEL=' "$ENV_FILE"; then
        awk -v v="$FULL_LLM_MODEL" '{ if (index($0, "LLM_MODEL=") == 1) print "LLM_MODEL=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    # Update MAX_CONTEXT / CTX_SIZE
    if grep -q '^MAX_CONTEXT=' "$ENV_FILE"; then
        awk -v v="$FULL_MAX_CONTEXT" '{ if (index($0, "MAX_CONTEXT=") == 1) print "MAX_CONTEXT=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    if grep -q '^CTX_SIZE=' "$ENV_FILE"; then
        awk -v v="$FULL_MAX_CONTEXT" '{ if (index($0, "CTX_SIZE=") == 1) print "CTX_SIZE=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    log ".env updated"
else
    fail ".env not found at $ENV_FILE"
fi

# ── Phase 4: Update models.ini ──
log "Updating models.ini..."
mkdir -p "$(dirname "$MODELS_INI")"
cat > "$MODELS_INI" << EOF
[${FULL_LLM_MODEL}]
filename = ${FULL_GGUF_FILE}
load-on-startup = true
n-ctx = ${FULL_MAX_CONTEXT}
EOF
log "models.ini updated"

# ── Phase 4b: Remove bootstrap model ──
# Lemonade's --extra-models-dir auto-discovers all GGUFs in /models and may
# load the bootstrap model instead of the full one specified in models.ini.
# Remove the bootstrap file to prevent this.
BOOTSTRAP_GGUF="${BOOTSTRAP_GGUF_FILE:-Qwen3.5-2B-Q4_K_M.gguf}"
BOOTSTRAP_PATH="$MODELS_DIR/$BOOTSTRAP_GGUF"
if [[ -f "$BOOTSTRAP_PATH" && "$FULL_GGUF_FILE" != "$BOOTSTRAP_GGUF" ]]; then
    log "Removing bootstrap model: $BOOTSTRAP_GGUF"
    rm -f "$BOOTSTRAP_PATH"
    log "Bootstrap model removed"
fi

# ── Phase 5: Hot-swap llama-server (if running) ──
# Read OLLAMA_PORT from .env (nohup doesn't inherit env vars from parent)
if [[ -f "$ENV_FILE" ]]; then
    OLLAMA_PORT=$(grep -E '^OLLAMA_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
fi

if [[ -n "$DOCKER_CMD" ]] && $DOCKER_CMD ps --filter name=dream-llama-server --format '{{.Names}}' 2>/dev/null | grep -q dream-llama-server; then
    log "Restarting llama-server with full model..."

    # Read GPU backend from .env (needed for health endpoint and restart strategy)
    _gpu_backend=""
    if [[ -f "$ENV_FILE" ]]; then
        _gpu_backend=$(grep -E '^GPU_BACKEND=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
    fi

    # Detect compose files
    COMPOSE_ARGS=()
    if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
        read -ra COMPOSE_ARGS <<< "$(cat "$INSTALL_DIR/.compose-flags")"
    elif [[ -f "$INSTALL_DIR/docker-compose.base.yml" ]]; then
        COMPOSE_ARGS=(-f "$INSTALL_DIR/docker-compose.base.yml")
        case "${_gpu_backend}" in
            nvidia) [[ -f "$INSTALL_DIR/docker-compose.nvidia.yml" ]] && COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.nvidia.yml") ;;
            amd)    [[ -f "$INSTALL_DIR/docker-compose.amd.yml" ]]    && COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.amd.yml") ;;
            apple)
                # On Darwin hosts the canonical macOS overlay lives at
                # installers/macos/docker-compose.macos.yml (native Metal llama-server
                # replicas: 0, llama-server-ready sidecar, host.docker.internal for
                # dashboard-api). The top-level docker-compose.apple.yml remains
                # valid for Linux hosts that select --gpu-backend apple (Lemonade).
                # Mirror the branch in scripts/resolve-compose-stack.sh so that the
                # .compose-flags fallback selects the same overlay the resolver does.
                if [[ "$(uname -s)" == "Darwin" && -f "$INSTALL_DIR/installers/macos/docker-compose.macos.yml" ]]; then
                    COMPOSE_ARGS+=(-f "$INSTALL_DIR/installers/macos/docker-compose.macos.yml")
                elif [[ -f "$INSTALL_DIR/docker-compose.apple.yml" ]]; then
                    COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.apple.yml")
                fi
                ;;
            # cpu or unknown: base only, no GPU overlay
        esac
    fi

    cd "$INSTALL_DIR" || fail "Cannot cd to $INSTALL_DIR"

    # Restart llama-server — strategy depends on GPU backend:
    # - AMD (Lemonade): use 'restart' to preserve cached llama-server build.
    #   Lemonade reads models.ini at startup, so it picks up the new model.
    # - NVIDIA/CPU (llama.cpp): force-recreate so the new GGUF_FILE in .env
    #   takes effect. `compose stop` + `compose up -d` is NOT enough — when the
    #   service has a stopped container, compose will start the existing
    #   container in place (preserving its baked --model arg) instead of
    #   building a fresh one from the updated .env. The original CMD points at
    #   /models/${BOOTSTRAP_GGUF_FILE}, which Phase 4b just deleted, so the
    #   container crash-loops. `--force-recreate --no-deps` guarantees a new
    #   container; --no-deps avoids touching other services in the project.
    log "Restarting llama-server container (backend: ${_gpu_backend:-unknown})..."
    # Both backends need a container *recreate*, not just a restart, so the
    # updated CTX_SIZE / MAX_CONTEXT / GGUF_FILE env values from the
    # freshly-bumped .env land in the new container. A plain `compose
    # restart` preserves the original env vars from when the container
    # first started — which on AMD/Lemonade leaves LEMONADE_CTX_SIZE pinned
    # at the bootstrap-tier value (e.g. 8192) even after .env says 131072.
    # The lemonade-entrypoint.sh wrapper then sees the stale env value,
    # never updates /root/.cache/lemonade/config.json, and Lemonade serves
    # the full model at the bootstrap context size — Hermes then returns
    # empty responses in ~1s because its 16k-token tool catalog overruns
    # the 8k context window. OpenCode hits the same wall.
    #
    # `env -u` strips the model-config vars from compose's shell so the
    # freshly-updated .env wins interpolation. Compose precedence is
    # shell-env > .env > compose default, and Phase 11 (parent of this
    # nohup'd script) sets the bootstrap-tier values as shell variables.
    #
    # Named volumes (lemonade-cache / lemonade-llama / lemonade-recipe on
    # AMD) survive --force-recreate, so the Lemonade binary cache + HF
    # cache + recipe state all persist across the recreate. The older
    # "AMD uses restart to preserve cached binary" comment was wrong —
    # named volumes are decoupled from the container lifecycle.
    if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
        env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE \
            $DOCKER_COMPOSE_CMD "${COMPOSE_ARGS[@]}" up -d --force-recreate --no-deps llama-server 2>&1 || true
    else
        # No compose flags — fall back to docker rm + manual recreate. On
        # NVIDIA the original CMD points at /models/${BOOTSTRAP_GGUF_FILE}
        # which Phase 4b just deleted; on AMD the container env still has
        # bootstrap-tier LEMONADE_CTX_SIZE. Either way `docker start`
        # would reuse the stale state, so we remove the container and ask
        # the operator to bring it back up via `dream restart` (which
        # re-runs compose with the latest .env).
        $DOCKER_CMD stop dream-llama-server 2>&1 || true
        $DOCKER_CMD rm dream-llama-server 2>&1 || true
        log "WARNING: .compose-flags not found — container removed. Run 'dream restart' to bring llama-server back up with the correct model + context size."
        write_status "failed"
    fi

    # Pick health endpoint based on GPU backend — Lemonade (AMD) serves
    # /api/v1/health, llama.cpp (NVIDIA/Apple/CPU) serves /health.
    if [[ "$_gpu_backend" == "amd" ]]; then
        _health_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/api/v1/health"
    else
        _health_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/health"
    fi

    # Wait for health (up to 5 minutes for the larger model to load)
    # For AMD/Lemonade: check that model_loaded is non-null in the JSON response.
    # Lemonade returns 200 with "model_loaded": null when no model is loaded yet.
    # Lemonade doesn't auto-load models from models.ini — it uses --extra-models-dir
    # for discovery but loads on-demand. We send a warm-up request to trigger loading.
    # For llama.cpp: a simple 200 check is sufficient — the server only starts
    # after loading the model specified in --model.
    log "Waiting for llama-server health at $_health_url ..."
    _healthy=false
    _warmup_sent=false
    for _i in $(seq 1 60); do
        _resp=$(curl -sf --max-time 5 "$_health_url" 2>/dev/null || echo "")
        if [[ -n "$_resp" ]]; then
            if [[ "$_gpu_backend" == "amd" ]]; then
                # Lemonade: verify a model is actually loaded, not just "status: ok"
                if echo "$_resp" | grep -q '"model_loaded"' && ! echo "$_resp" | grep -q '"model_loaded": *null'; then
                    _healthy=true
                    break
                fi
                # Lemonade is healthy but no model loaded — send a warm-up request
                # to trigger on-demand loading of the new model. Lemonade caches the
                # previously-loaded model name across restarts, which fails after the
                # bootstrap GGUF is deleted. This request forces it to load the new one.
                # Retry every 15s — the first request may fail if Lemonade isn't fully
                # ready to accept chat completions yet.
                if [[ "$_warmup_sent" == "false" ]] || (( _i % 3 == 0 )); then
                    # Escape any double-quotes in the filename so the JSON body
                    # below stays well-formed even for non-standard library entries.
                    # Mirrors the _safe_model pattern in write_status() above.
                    _model_id="extra.${FULL_GGUF_FILE//\"/\\\"}"
                    log "Sending warm-up request to trigger model loading: $_model_id (attempt $_i/60)"
                    if curl -sf --max-time 30 -X POST \
                        "http://127.0.0.1:${OLLAMA_PORT:-8080}/api/v1/chat/completions" \
                        -H "Content-Type: application/json" \
                        -d "{\"model\":\"${_model_id}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":1}" \
                        &>/dev/null; then
                        _warmup_sent=true
                        log "Warm-up request accepted — waiting for model to finish loading"
                    fi
                fi
                log "Lemonade healthy but no model loaded yet (attempt $_i/60)"
            else
                # llama.cpp: 200 means model is loaded
                _healthy=true
                break
            fi
        fi
        sleep 5
    done

    # Assert the recreated container's --model arg actually points at the new
    # GGUF file. If compose handed us back a started-not-recreated container
    # (the bug --force-recreate above is meant to prevent), the health check
    # may still pass on Lemonade (which loads on demand) but llama.cpp will
    # crash-loop the moment the next request hits, because Phase 4b has
    # already deleted the bootstrap GGUF the baked CMD refers to. Fail loudly
    # so the operator does not discover this hours later via a 502.
    if [[ "$_gpu_backend" != "amd" ]] && [[ -n "$DOCKER_CMD" ]]; then
        _running_cmd=$($DOCKER_CMD inspect dream-llama-server --format '{{join .Config.Cmd " "}}' 2>/dev/null || echo "")
        if [[ -z "$_running_cmd" ]]; then
            log "ERROR: could not inspect llama-server container command after recreate."
            log "  Recover with: cd $INSTALL_DIR && docker compose \$(cat .compose-flags) up -d --force-recreate --no-deps llama-server"
            write_status "failed"
            fail "llama-server command inspection failed after force-recreate."
        elif ! [[ "$_running_cmd" == *"/models/${FULL_GGUF_FILE}"* ]]; then
            log "ERROR: llama-server container started with stale --model arg."
            log "  expected /models/${FULL_GGUF_FILE}, got: $_running_cmd"
            log "  This means 'compose up -d --force-recreate' did not pick up the updated .env."
            # Dump what compose would have seen so a future regression can be
            # diagnosed from logs alone. If any of these have non-empty values,
            # they overrode the .env at compose interpolation time.
            for _k in GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE; do
                _v="$(printenv "$_k" 2>/dev/null || true)"
                if [[ -n "$_v" ]]; then
                    log "  shell env leak: $_k=$_v (overrode .env's $_k)"
                fi
            done
            log "  .env now has:"
            for _k in GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE; do
                log "    $(grep -E "^${_k}=" "$ENV_FILE" 2>/dev/null || echo "${_k}=<missing>")"
            done
            log "  Recover with: cd $INSTALL_DIR && env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE docker compose \$(cat .compose-flags) up -d --force-recreate --no-deps llama-server"
            write_status "failed"
            fail "llama-server container started with stale --model arg after force-recreate."
        fi
    fi

    if $_healthy; then
        log "SUCCESS: llama-server is running with $FULL_LLM_MODEL"
        # Regenerate lemonade.yaml with the new model ID and restart LiteLLM.
        # Lemonade exposes models as "extra.<GGUF_FILE>" — the config must
        # reference the exact ID, not a wildcard passthrough.
        if $DOCKER_CMD ps --filter name=dream-litellm --format '{{.Names}}' 2>/dev/null | grep -q dream-litellm; then
            log "Updating LiteLLM config for new model: extra.${FULL_GGUF_FILE}"
            # Read per-install lemonade key from .env; fall back to literal so
            # older installs without the key still produce a valid config (lemonade
            # itself ignores the value).
            LITELLM_LEMONADE_API_KEY=$(grep '^LITELLM_LEMONADE_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"\047\r')
            : "${LITELLM_LEMONADE_API_KEY:=sk-lemonade}"
            # extra_body.chat_template_kwargs.enable_thinking=false is what
            # un-blocks Perplexica + any other client that doesn't manually
            # prepend `/no_think` to its prompts. Qwen3 thinking models
            # (qwen3-coder-next, Qwen3.6-35B-A3B, Qwen3-30B-A3B) emit a
            # <think>...</think> reasoning block before the answer, which on
            # Strix Halo with a long Perplexica synthesis prompt can run for
            # minutes — the user sees the GPU spin up but no tokens reach the
            # UI until the close-think tag fires. Passing
            # `chat_template_kwargs: {enable_thinking: false}` to llama.cpp /
            # Lemonade hits the Qwen3 chat template's enable_thinking switch
            # and skips the reasoning block entirely.
            #
            # Verified 2026-05-20 on Strix Halo (AMD/Lemonade): same prompt
            # went from "hangs indefinitely" to 1.8s end-to-end with this
            # one block added. The kwarg is a Qwen3-specific switch and is
            # safely ignored by non-Qwen3 chat templates.
            _renderer_ok=false
            _renderer_script="$INSTALL_DIR/scripts/render-runtime-configs.py"
            _renderer_py="${DREAM_PYTHON_CMD:-}"
            if [[ -z "$_renderer_py" && -f "$INSTALL_DIR/lib/python-cmd.sh" ]]; then
                . "$INSTALL_DIR/lib/python-cmd.sh"
                _renderer_py="$(ds_detect_python_cmd 2>/dev/null || true)"
            fi
            if [[ -z "$_renderer_py" ]]; then
                _renderer_py="python3"
            fi
            if [[ -f "$_renderer_script" ]] && command -v "$_renderer_py" >/dev/null 2>&1; then
                if "$_renderer_py" "$_renderer_script" \
                    --surface litellm-lemonade \
                    --dream-mode lemonade \
                    --gpu-backend amd \
                    --gguf-file "$FULL_GGUF_FILE" \
                    --litellm-key "$LITELLM_LEMONADE_API_KEY" \
                    --output-root "$INSTALL_DIR" \
                    --write >/dev/null 2>&1; then
                    _renderer_ok=true
                else
                    log "WARNING: Runtime config renderer failed for LiteLLM; falling back to inline writer"
                fi
            fi
            if [[ "$_renderer_ok" != "true" ]]; then
                cat > "$INSTALL_DIR/config/litellm/lemonade.yaml" << LITELLM_UPGRADE_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/extra.${FULL_GGUF_FILE}
      api_base: http://llama-server:8080/api/v1
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/extra.${FULL_GGUF_FILE}
      api_base: http://llama-server:8080/api/v1
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 120
  stream_timeout: 60
LITELLM_UPGRADE_EOF
            fi
            unset _renderer_ok _renderer_script _renderer_py
            log "Restarting LiteLLM to pick up model change..."
            $DOCKER_CMD restart dream-litellm 2>&1 || log "WARNING: LiteLLM restart failed (non-fatal)"
        fi
        # Recreate OpenClaw so inject-token.js picks up the new GGUF_FILE/LLM_MODEL
        # from .env. A restart alone won't work — env vars are baked in at container
        # creation time, and inject-token.js builds the Lemonade model name from them.
        # Strip the same model-config vars here as the llama-server recreate path
        # so shell-env pollution cannot override the freshly-updated .env.
        if $DOCKER_CMD ps --filter name=dream-openclaw --format '{{.Names}}' 2>/dev/null | grep -q dream-openclaw; then
            log "Recreating OpenClaw to pick up model change..."
            # Guard on BOTH compose args AND a non-empty $DOCKER_COMPOSE_CMD —
            # mirrors the llama-server hot-swap contract above (the
            # `${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD"` checks).
            # If $DOCKER_COMPOSE_CMD is empty (no compose v2 plugin AND no
            # docker-compose v1 binary), expanding it as the command word
            # would turn the line into `"${COMPOSE_ARGS[@]}" up -d ...`,
            # which executes the first compose-arg (e.g. `-f`) as a binary.
            # Skip the recreate and surface a clear warning instead.
            if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
                env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE \
                    $DOCKER_COMPOSE_CMD "${COMPOSE_ARGS[@]}" up -d --force-recreate openclaw 2>&1 || \
                    log "WARNING: OpenClaw recreate failed (non-fatal)"
            else
                log "WARNING: No compose binary available (DOCKER_COMPOSE_CMD empty or compose args missing) — OpenClaw was NOT recreated. The new model will not take effect until OpenClaw is recreated manually with: env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE docker compose up -d --force-recreate openclaw"
            fi
        fi
        # Patch Hermes Agent's config so it stops asking the LLM server for the
        # bootstrap model id. PR #1191 substitutes model.default in the template
        # at install time, but at install time we've only loaded the bootstrap
        # model (Qwen3.5-2B) — Hermes's /opt/data/config.yaml is therefore
        # pinned to that name. Once this script swaps Lemonade/llama-server
        # to the full model, Hermes keeps sending the stale bootstrap id and
        # every chat completion 404s.
        #
        # This is hard-broken on AMD/Lemonade (which strictly validates the
        # `model` field) and silently masked on NVIDIA/Apple (llama.cpp
        # ignores the field and serves whatever's loaded), so the bug
        # surfaces as "Hermes works on Tower2/Mac but every prompt 404s on
        # Strix Halo" after a bootstrap-to-full swap.
        #
        # Two files to keep in sync:
        #   1. /opt/data/config.yaml inside the container — the live config
        #      Hermes reads at startup. Owned by container UID; patch via
        #      `docker exec` so we don't need host sudo.
        #   2. extensions/services/hermes/cli-config.yaml.template — the
        #      source Hermes copies into /opt/data on first start. Updating
        #      it keeps subsequent down-and-up cycles correct.
        # Lemonade prefixes the served model id with "extra."; llama.cpp
        # serves under the bare file name. Mirror the same branch PR #1191
        # added in installers/phases/11-services.sh.
        _hermes_old_model="$BOOTSTRAP_GGUF_FILE"
        _hermes_new_model="$FULL_GGUF_FILE"
        _gpu_backend_for_hermes=$(grep -E '^GPU_BACKEND=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "")
        if [[ "$_gpu_backend_for_hermes" == "amd" ]]; then
            _hermes_old_model="extra.$BOOTSTRAP_GGUF_FILE"
            _hermes_new_model="extra.$FULL_GGUF_FILE"
        fi
        log "Patching Hermes config: model.default $_hermes_old_model -> $_hermes_new_model"

        # Template on host (user-owned, no sudo needed). Patch this even when
        # Hermes is stopped so future container creates do not copy the stale
        # bootstrap model id.
        _hermes_tpl="$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template"
        if [[ -f "$_hermes_tpl" ]]; then
            _hermes_patcher="$INSTALL_DIR/scripts/patch-hermes-config.py"
            _python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
            if [[ -n "$_python_cmd" && -f "$_hermes_patcher" ]]; then
                if ! "$_python_cmd" "$_hermes_patcher" "$_hermes_tpl" \
                    --model "$_hermes_new_model" \
                    --context-length "$FULL_MAX_CONTEXT" 2>&1; then
                    log "WARNING: Could not patch ${_hermes_tpl} with patch-hermes-config.py (non-fatal — operator can hand-edit before restarting Hermes)"
                fi
            elif sed -i.bak \
                -e "s|^  default: \"${_hermes_old_model}\"|  default: \"${_hermes_new_model}\"|" \
                -e "s|^  context_length: .*|  context_length: ${FULL_MAX_CONTEXT}|" \
                -e "s|^    context_length: .*|    context_length: ${FULL_MAX_CONTEXT}|" \
                "$_hermes_tpl" 2>&1; then
                rm -f "${_hermes_tpl}.bak"
            else
                log "WARNING: Could not patch ${_hermes_tpl} (non-fatal — operator can hand-edit before restarting Hermes)"
            fi
        fi

        if $DOCKER_CMD ps --filter name=dream-hermes --format '{{.Names}}' 2>/dev/null | grep -q dream-hermes; then
            # Live config inside the running container (owned by container UID).
            $DOCKER_CMD exec dream-hermes sh -c \
                "sed -i -e 's|^  default: \"${_hermes_old_model}\"|  default: \"${_hermes_new_model}\"|' -e 's|^  context_length: .*|  context_length: ${FULL_MAX_CONTEXT}|' -e 's|^    context_length: .*|    context_length: ${FULL_MAX_CONTEXT}|' -e 's|^  enabled: .*|  enabled: true|' -e 's|^  threshold: .*|  threshold: 0.75|' -e 's|^  target_ratio: .*|  target_ratio: 0.50|' -e 's|^  protect_last_n: .*|  protect_last_n: 40|' /opt/data/config.yaml" 2>&1 || \
                log "WARNING: Could not patch Hermes /opt/data/config.yaml (non-fatal — operator can hand-edit and 'docker restart dream-hermes')"
            log "Restarting Hermes to pick up model change..."
            $DOCKER_CMD restart dream-hermes 2>&1 || log "WARNING: Hermes restart failed (non-fatal — hand-restart with 'docker restart dream-hermes')"

            # Pre-warm the freshly-swapped LLM + Hermes's 14K-token system prompt.
            #
            # Two latency hits if we skip this:
            #   1. llama-server / Lemonade loads the full model into VRAM on first
            #      request (`--n-gpu-layers 999` is lazy). PR #1192 already warms
            #      this at install time, but that warm-up was against the
            #      bootstrap model — after the swap, the slot is cold again.
            #   2. Hermes's runtime config bakes a 14K-token system prompt
            #      (skills, soul, tool descriptors). First Hermes prompt has
            #      to prefill all of it. Empirically 67s on Strix Halo,
            #      1m25s on macOS, ~5s once cached. We've seen real users
            #      think Hermes is broken because they alt-tabbed away during
            #      a fresh install and the first prompt looked stuck.
            #
            # Mirrors PR #1192's pattern: best-effort, time-bounded, never fails
            # the upgrade. If either warm-up times out the swap still succeeds —
            # the user just eats the slow first call.
            log "Pre-warming llama-server slot with full model..."
            _prewarm_api_path="/v1"
            _prewarm_model="$FULL_GGUF_FILE"
            if [[ "$_gpu_backend_for_hermes" == "amd" ]]; then
                _prewarm_api_path="/api/v1"
                _prewarm_model="extra.$FULL_GGUF_FILE"
            fi
            _prewarm_body="{\"model\":\"${_prewarm_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}"
            if $DOCKER_CMD exec dream-hermes curl -sf --max-time 120 -X POST \
                "http://llama-server:8080${_prewarm_api_path}/chat/completions" \
                -H "Content-Type: application/json" \
                -d "$_prewarm_body" >/dev/null 2>&1; then
                log "llama-server slot pre-warmed."
            else
                log "WARNING: llama-server pre-warm timed out — first Hermes prompt may be slow."
            fi

            # Wait for Hermes to come back up after the restart, then trigger
            # one no-op invocation so the 14K system prompt gets into
            # llama-server's KV cache. We cap at 90s total — long enough for
            # Hermes's skills sync + config bootstrap (start_period: 60s in
            # compose.yaml) plus a few decode tokens, short enough that a
            # broken Hermes doesn't stall the script forever.
            log "Pre-warming Hermes system prompt (caches 14K-token prefill)..."
            _hermes_ready=false
            for _i in $(seq 1 30); do
                if $DOCKER_CMD exec dream-hermes curl -sf --max-time 3 http://127.0.0.1:9119/api/status >/dev/null 2>&1; then
                    _hermes_ready=true
                    break
                fi
                sleep 2
            done
            if $_hermes_ready; then
                if $DOCKER_CMD exec dream-hermes timeout 90 \
                    /opt/hermes/.venv/bin/hermes -z "ping" --yolo \
                    >/dev/null 2>&1; then
                    log "Hermes system prompt cached — first user prompt will be fast."
                else
                    log "WARNING: Hermes warm-up timed out (>90s). First user prompt will incur the full 14K-token prefill."
                fi
            else
                log "WARNING: Hermes did not respond on /api/status within 60s; skipping system-prompt warm-up."
            fi
        else
            # If Hermes is stopped, its persisted /opt/data mount may still
            # exist on the host. Patch it when writable; otherwise the template
            # update above still protects first-time/fresh data starts.
            _hermes_live="$INSTALL_DIR/data/hermes/config.yaml"
            if [[ -f "$_hermes_live" ]]; then
                if [[ -n "${_python_cmd:-}" && -f "${_hermes_patcher:-}" ]]; then
                    if ! "$_python_cmd" "$_hermes_patcher" "$_hermes_live" \
                        --model "$_hermes_new_model" \
                        --context-length "$FULL_MAX_CONTEXT" 2>&1; then
                        log "WARNING: Could not patch ${_hermes_live} with patch-hermes-config.py (non-fatal — operator can hand-edit and restart Hermes)"
                    fi
                elif sed -i.bak \
                    -e "s|^  default: \"${_hermes_old_model}\"|  default: \"${_hermes_new_model}\"|" \
                    -e "s|^  context_length: .*|  context_length: ${FULL_MAX_CONTEXT}|" \
                    -e "s|^    context_length: .*|    context_length: ${FULL_MAX_CONTEXT}|" \
                    "$_hermes_live" 2>&1; then
                    rm -f "${_hermes_live}.bak"
                else
                    log "WARNING: Could not patch ${_hermes_live} (non-fatal — operator can hand-edit and restart Hermes)"
                fi
            fi
        fi
        sync_windows_opencode_config
    else
        log "WARNING: llama-server health check timed out. The model may still be loading."
        log "Check: docker logs dream-llama-server"
    fi
elif [[ -f "$INSTALL_DIR/data/.llama-server.pid" ]]; then
    # macOS native llama-server (Metal) — restart with new model
    log "Detected native llama-server (macOS Metal mode)"

    LLAMA_SERVER_BIN="$INSTALL_DIR/bin/llama-server"
    LLAMA_SERVER_PID_FILE="$INSTALL_DIR/data/.llama-server.pid"
    LLAMA_SERVER_LOG="$INSTALL_DIR/data/llama-server.log"

    if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
        log "WARNING: llama-server binary not found at $LLAMA_SERVER_BIN. Cannot hot-swap."
        log "Run './dream-macos.sh restart' to load the new model manually."
    else
        # Read updated model config from .env
        _gguf_file=$(grep '^GGUF_FILE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'")
        _ctx_size=$(grep '^CTX_SIZE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'" || echo "")
        [[ -z "$_ctx_size" ]] && _ctx_size=$(grep '^MAX_CONTEXT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'" || echo "")
        [[ -z "$_ctx_size" ]] && _ctx_size="16384"
        _model_path="$MODELS_DIR/${_gguf_file}"

        if [[ ! -f "$_model_path" ]]; then
            log "WARNING: Model file not found at $_model_path"
        else
            # Capture old model path for rollback before we kill the process
            _old_pid=$(cat "$LLAMA_SERVER_PID_FILE" 2>/dev/null | tr -d '[:space:]')
            _old_model_path=""
            if [[ -n "$_old_pid" ]] && kill -0 "$_old_pid" 2>/dev/null; then
                _old_model_path=$(ps -p "$_old_pid" -o args= 2>/dev/null | grep -oE '\-\-model [^ ]+' | awk '{print $2}') || true
            fi

            # Stop existing native llama-server
            if [[ -n "$_old_pid" ]] && kill -0 "$_old_pid" 2>/dev/null; then
                # Verify it's actually llama-server (PID could have been reused)
                if ps -p "$_old_pid" -o comm= 2>/dev/null | grep -q llama; then
                    log "Stopping native llama-server (PID $_old_pid)..."
                    kill "$_old_pid" 2>/dev/null || true
                    sleep 2
                    if kill -0 "$_old_pid" 2>/dev/null; then
                        kill -9 "$_old_pid" 2>/dev/null || true
                    fi
                else
                    log "PID $_old_pid is no longer llama-server, skipping kill"
                fi
            fi

            # Read reasoning mode from .env (default off to prevent thinking models
            # from consuming the entire token budget on internal reasoning)
            _reasoning=$(grep '^LLAMA_REASONING=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "")
            [[ -z "$_reasoning" ]] && _reasoning="off"
            case "$_reasoning" in
                off)  _reasoning_fmt="none" ;;
                on)   _reasoning_fmt="deepseek" ;;
                *)    _reasoning_fmt="$_reasoning" ;;
            esac

            # Honour the unified BIND_ADDRESS knob (PR #964); empty/missing → loopback.
            _bind=$(grep '^BIND_ADDRESS=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            [[ -z "$_bind" ]] && _bind="127.0.0.1"
            _flash_attn=$(grep '^LLAMA_ARG_FLASH_ATTN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _cache_type_k=$(grep '^LLAMA_ARG_CACHE_TYPE_K=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _cache_type_v=$(grep '^LLAMA_ARG_CACHE_TYPE_V=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _n_cpu_moe=$(grep '^LLAMA_ARG_N_CPU_MOE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _spec_type=$(grep '^LLAMA_ARG_SPEC_TYPE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _spec_draft_n_max=$(grep '^LLAMA_ARG_SPEC_DRAFT_N_MAX=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _llama_args=(
                --host "$_bind" --port 8080
                --model "$_model_path"
                --ctx-size "$_ctx_size"
                --n-gpu-layers 999
                --reasoning-format "$_reasoning_fmt"
                --metrics
            )
            [[ -n "$_flash_attn" ]] && _llama_args+=(--flash-attn "$_flash_attn")
            [[ -n "$_cache_type_k" ]] && _llama_args+=(--cache-type-k "$_cache_type_k")
            [[ -n "$_cache_type_v" ]] && _llama_args+=(--cache-type-v "$_cache_type_v")
            [[ -n "$_n_cpu_moe" ]] && _llama_args+=(--n-cpu-moe "$_n_cpu_moe")
            [[ -n "$_spec_type" ]] && _llama_args+=(--spec-type "$_spec_type")
            [[ -n "$_spec_draft_n_max" ]] && _llama_args+=(--spec-draft-n-max "$_spec_draft_n_max")

            # Relaunch with new model
            log "Starting native llama-server with ${_gguf_file}..."
            "$LLAMA_SERVER_BIN" "${_llama_args[@]}" > "$LLAMA_SERVER_LOG" 2>&1 &
            _new_pid=$!
            echo "$_new_pid" > "$LLAMA_SERVER_PID_FILE"

            # Wait for health
            log "Waiting for native llama-server health..."
            _healthy=false
            for _i in $(seq 1 60); do
                if curl -sf --max-time 5 "http://127.0.0.1:8080/health" &>/dev/null; then
                    _healthy=true
                    break
                fi
                sleep 5
            done

            if $_healthy; then
                log "SUCCESS: Native llama-server running with ${_gguf_file} (PID $_new_pid)"
            else
                log "WARNING: New model failed to load. Attempting rollback..."
                kill "$_new_pid" 2>/dev/null || true
                sleep 2
                if kill -0 "$_new_pid" 2>/dev/null; then
                    kill -9 "$_new_pid" 2>/dev/null || true
                fi
                if [[ -n "${_old_model_path:-}" && -f "$_old_model_path" ]]; then
                    "$LLAMA_SERVER_BIN" \
                        --host "$_bind" --port 8080 \
                        --model "$_old_model_path" \
                        --ctx-size "$_ctx_size" \
                        --n-gpu-layers 999 \
                        --reasoning-format "${_reasoning_fmt:-none}" \
                        --metrics \
                        > "$LLAMA_SERVER_LOG" 2>&1 &
                    _rollback_pid=$!
                    echo "$_rollback_pid" > "$LLAMA_SERVER_PID_FILE"
                    log "Rolled back to previous model: $(basename "$_old_model_path") (PID $_rollback_pid)"
                else
                    log "WARNING: Could not rollback — previous model not found."
                    log "Run './dream-macos.sh restart' to manually recover."
                fi
            fi
        fi
    fi
else
    log "Docker services not running. Config updated — full model will load on next start."
fi

# ── Phase 5c: Update Perplexica's defaultChatModel ──
# Phase 12 of the installer configures Perplexica with whatever LLM_MODEL was
# in scope at that time — which on bootstrap installs is the bootstrap model
# name (e.g. qwen3.5-2b), NOT the full model. Without an update here,
# Perplexica's settings.preferences.defaultChatModel stays "qwen3.5-2b"
# forever, even after the hot-swap replaces the underlying GGUF. The UI
# shows the wrong model name in the dropdown, and the chatModels list under
# the OpenAI provider keeps the bootstrap entry instead of the full model.
#
# Requests still functionally route via LiteLLM's `*` wildcard or llama.cpp's
# served-model-passthrough, so this hasn't been a hard failure — but it's a
# cosmetic + future-proofing issue (a non-wildcard router would 404 the
# stale model id). Mirror the install-time logic from
# installers/phases/12-health.sh:194-238: update modelProviders + preferences
# via Perplexica's `/api/config` PUT endpoint.
PERPLEXICA_PORT=$(grep -E '^PERPLEXICA_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r')
: "${PERPLEXICA_PORT:=3004}"
_perplexica_url="http://127.0.0.1:${PERPLEXICA_PORT}"
if curl -sf --max-time 3 "${_perplexica_url}/api/config" >/dev/null 2>&1; then
    log "Updating Perplexica config to point at ${FULL_LLM_MODEL}..."
    _py_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
    if [[ -n "$_py_cmd" ]]; then
        # On Lemonade, LiteLLM exposes the model id as "extra.<GGUF_FILE>".
        # On NVIDIA/Apple/CPU, llama.cpp serves under the bare model id —
        # Phase 12 picks the friendly LLM_MODEL string, so do the same.
        _px_model="$FULL_LLM_MODEL"
        _gpu_backend_for_perplexica=$(grep -E '^GPU_BACKEND=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "")
        if [[ "$_gpu_backend_for_perplexica" == "amd" ]]; then
            _px_model="extra.$FULL_GGUF_FILE"
        fi
        _litellm_key=$(grep -E '^LITELLM_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "no-key")
        : "${_litellm_key:=no-key}"
        if curl -sf --max-time 3 "${_perplexica_url}/api/config" 2>/dev/null | \
            PERPLEXICA_URL="$_perplexica_url" \
            PX_MODEL="$_px_model" \
            PX_KEY="$_litellm_key" \
            "$_py_cmd" -c '
import os, sys, json, urllib.request
config = json.load(sys.stdin)["values"]
providers = config.get("modelProviders", [])
openai_prov = next((p for p in providers if p["type"] == "openai"), None)
if not openai_prov:
    sys.exit(0)  # Perplexica has no OpenAI provider configured; skip (non-fatal)
url = os.environ["PERPLEXICA_URL"] + "/api/config"
model = os.environ["PX_MODEL"]
key = os.environ["PX_KEY"]
def post(k, v):
    data = json.dumps({"key": k, "value": v}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)
openai_prov["chatModels"] = [{"key": model, "name": model}]
# Preserve baseURL on the provider config but refresh the key
prov_config = openai_prov.get("config") or {}
prov_config["apiKey"] = key
openai_prov["config"] = prov_config
post("modelProviders", providers)
prefs = config.get("preferences", {})
prefs["defaultChatModel"] = model
prefs["defaultChatProvider"] = openai_prov["id"]
post("preferences", prefs)
print("ok")
' >/dev/null 2>&1; then
            log "Perplexica defaultChatModel updated to ${_px_model}."
        else
            log "WARNING: Perplexica config update failed (non-fatal — defaultChatModel may still read the bootstrap value)"
        fi
    else
        log "WARNING: python3 not found, skipping Perplexica config update"
    fi
fi

# ── Phase 6: Restart host agent (if running) ──
# The host agent may cache stale state — restart it so it picks up the new
# model config and any updated endpoints.
if command -v systemctl &>/dev/null && systemctl --user is-active dream-host-agent.service &>/dev/null; then
    log "Restarting dream-host-agent (systemd)..."
    systemctl --user restart dream-host-agent.service 2>&1 || \
        log "WARNING: Could not restart host agent (non-fatal)"
elif [[ -f "$HOME/Library/LaunchAgents/com.dreamserver.host-agent.plist" ]]; then
    log "Restarting dream-host-agent (launchctl)..."
    launchctl kickstart -k "gui/$(id -u)/com.dreamserver.host-agent" 2>&1 || \
        log "WARNING: Could not restart host agent (non-fatal)"
fi

log "Bootstrap upgrade complete."
