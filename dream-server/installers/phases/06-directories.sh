#!/bin/bash
# ============================================================================
# Dream Server Installer — Phase 06: Directories & Configuration
# ============================================================================
# Part of: installers/phases/
# Purpose: Create directories, copy source files, generate .env, configure
#          OpenClaw, SearXNG, and validate .env schema
#
# Expects: SCRIPT_DIR, INSTALL_DIR, LOG_FILE, DRY_RUN, INTERACTIVE,
#           TIER, TIER_NAME, VERSION, GPU_BACKEND, SYSTEM_TZ,
#           LLM_MODEL, MAX_CONTEXT, GGUF_FILE, COMPOSE_FLAGS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_HERMES, ENABLE_OPENCLAW,
#           OPENCLAW_CONFIG, OPENCLAW_PROVIDER_NAME_DEFAULT,
#           OPENCLAW_PROVIDER_URL_DEFAULT, GPU_ASSIGNMENT_JSON,
#           COMFYUI_GPU_UUID, WHISPER_GPU_UUID, EMBEDDINGS_GPU_UUID,
#           LLAMA_SERVER_GPU_UUIDS, LLAMA_ARG_SPLIT_MODE, LLAMA_ARG_TENSOR_SPLIT,
#           chapter(), ai(), ai_ok(), ai_warn(), log(), warn(), error()
# Provides: WEBUI_SECRET, N8N_PASS, LITELLM_KEY, LIVEKIT_SECRET,
#           DASHBOARD_API_KEY, SHIELD_API_KEY, OPENCODE_SERVER_PASSWORD,
#           OPENCLAW_TOKEN, OPENCLAW_PROVIDER_NAME, OPENCLAW_PROVIDER_URL,
#           OPENCLAW_MODEL, OPENCLAW_CONTEXT, GPU_ASSIGNMENT_JSON_B64 (in .env)
#
# Modder notes:
#   This is the largest phase. Modify .env generation, add new config files,
#   or change directory layout here.
# ============================================================================

dream_progress 38 "directories" "Preparing installation directory"
chapter "SETTING UP INSTALLATION"

if $DRY_RUN; then
    log "[DRY RUN] Would create: $INSTALL_DIR/{config,data,models}"
    log "[DRY RUN] Would copy compose files ($COMPOSE_FLAGS) and source tree"
    log "[DRY RUN] Would generate .env with secrets (WEBUI_SECRET, N8N_PASS, LITELLM_KEY, etc.)"
    log "[DRY RUN] Would generate SearXNG config with randomized secret key"
    [[ "$ENABLE_HERMES" == "true" ]] && log "[DRY RUN] Would configure Hermes Agent (LLM endpoint: http://llama-server:8080/v1; data dir: $INSTALL_DIR/data/hermes)"
    [[ "$ENABLE_OPENCLAW" == "true" ]] && log "[DRY RUN] Would configure OpenClaw (model: $LLM_MODEL, config: ${OPENCLAW_CONFIG:-default})"
    log "[DRY RUN] Would validate .env against schema"
else
    # Create directories
    dream_progress 38 "directories" "Creating directory structure"
    mkdir -p "$INSTALL_DIR"/{config,data,models}
    mkdir -p "$INSTALL_DIR"/data/{open-webui,whisper,tts,n8n,qdrant,models,privacy-shield,ape,token-spy,hermes}
    mkdir -p "$INSTALL_DIR"/data/hermes-proxy/{caddy-data,caddy-config}
    mkdir -p "$INSTALL_DIR"/data/langfuse/{postgres,clickhouse,redis,minio}
    mkdir -p "$INSTALL_DIR"/config/{n8n,litellm,openclaw,searxng}

    # Fix ownership of data/config dirs that may have been created by containers
    # (e.g. SearXNG runs as uid 977, ComfyUI data owned by root)
    for _data_dir in "$INSTALL_DIR"/data/*/; do
        if [[ -d "$_data_dir" ]] && ! [[ -w "$_data_dir" ]]; then
            sudo chown -R "$(id -u):$(id -g)" "$_data_dir" 2>/dev/null || true
        fi
    done
    for _cfg_dir in "$INSTALL_DIR"/config/*/; do
        if [[ -d "$_cfg_dir" ]] && ! [[ -w "$_cfg_dir" ]]; then
            sudo chown -R "$(id -u):$(id -g)" "$_cfg_dir" 2>/dev/null || true
        fi
    done

    # Ensure we can write to config/data subtrees (rsync will fail otherwise)
    if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
        _cant_write=""
        for _root in config data; do
            [[ -d "$INSTALL_DIR/$_root" ]] || continue
            for _d in "$INSTALL_DIR/$_root"/*/; do
                [[ -d "$_d" ]] && ! [[ -w "$_d" ]] && _cant_write="$_cant_write ${_d#$INSTALL_DIR/}"
            done
        done
        if [[ -n "$_cant_write" ]]; then
            error "Cannot write to directories (likely container-owned):$_cant_write

Fix with: sudo chown -R \$(id -u):\$(id -g) $INSTALL_DIR/config $INSTALL_DIR/data — then re-run the installer."
        fi
    fi

    # Copy entire source tree to install dir (skip if same directory)
    dream_progress 39 "directories" "Copying source files"
    if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
        ai "Copying source files to $INSTALL_DIR..."
        if command -v rsync &>/dev/null; then
            rsync -a --no-owner --no-group \
                --exclude='.git' \
                --exclude='data/' \
                --exclude='logs/' \
                --exclude='models/' \
                --exclude='.env' \
                --exclude='node_modules/' \
                --exclude='dist/' \
                --exclude='*.log' \
                --exclude='.current-mode' \
                --exclude='.profiles' \
                --exclude='.target-model' \
                --exclude='.target-quantization' \
                --exclude='.offline-mode' \
                "$SCRIPT_DIR/" "$INSTALL_DIR/"
        else
            # Fallback: cp -r everything, then remove runtime artifacts
            if ! cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/" 2>>"$LOG_FILE"; then
                warn "Source copy incomplete — some files may be missing"
            fi
            if ! cp "$SCRIPT_DIR"/.gitignore "$INSTALL_DIR/" 2>>"$LOG_FILE"; then
                warn "Failed to copy .gitignore"
            fi
            rm -rf "$INSTALL_DIR/.git" 2>>"$LOG_FILE" || true
        fi
        # Ensure scripts are executable
        chmod +x "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/dream-cli 2>>"$LOG_FILE" || warn "Some scripts may not be executable — verify after install"
        ai_ok "Source files installed"
    else
        log "Running in-place (source == install dir), skipping file copy"
    fi

    # DreamForge was retired from the shipped stack after Hermes became the
    # default agent surface. Existing installs may still contain the old
    # bundled extension because the source copy above does not prune removed
    # files. Delete only the retired service code so stale compose files cannot
    # be picked up; preserve data/dreamforge for users who want to archive it.
    _retired_dreamforge_dir="$INSTALL_DIR/extensions/services/dreamforge"
    if [[ -d "$_retired_dreamforge_dir" ]]; then
        rm -rf "$_retired_dreamforge_dir"
        log "Removed retired DreamForge service files from extensions/services"
    fi

    # Copy extensions library to data dir for dashboard portal.
    # Source resolution: dev installs and full checkouts read the product-owned
    # library under extensions/library/. Bootstrap installs also get the same
    # templates bundled by get-dream-server.sh under extensions-library-bundle/.
    # Without one of these paths, dashboard-api's /api/extensions/{id}/install
    # endpoint returns 503 "Extensions library is unavailable" and the
    # dashboard's Extensions page is non-functional.
    _ext_lib_src=""
    for _candidate in \
        "$SCRIPT_DIR/extensions/library/services" \
        "$INSTALL_DIR/extensions/library/services" \
        "$INSTALL_DIR/extensions-library-bundle/services"
    do
        if [[ -d "$_candidate" ]]; then _ext_lib_src="$_candidate"; break; fi
    done
    if [[ -n "$_ext_lib_src" ]]; then
        mkdir -p "$INSTALL_DIR/data/extensions-library"
        cp -r "$_ext_lib_src/." "$INSTALL_DIR/data/extensions-library/"
        ai_ok "Extensions library copied to data/extensions-library/ (from $_ext_lib_src)"
    else
        ai_warn "Extensions library not found; dashboard Extensions page will return 503 until populated"
    fi

    # Select tier-appropriate OpenClaw config
    if [[ "$ENABLE_OPENCLAW" == "true" && -n "$OPENCLAW_CONFIG" ]]; then
        OPENCLAW_MODEL="$LLM_MODEL"
        OPENCLAW_CONTEXT=$MAX_CONTEXT

        # Tiers 1/2/3 set OPENCLAW_CONFIG="openclaw.json", which is also the
        # destination filename — skip the self-copy in that case so the file
        # the rsync from SCRIPT_DIR placed there is used as-is.
        _oc_src="$INSTALL_DIR/config/openclaw/$OPENCLAW_CONFIG"
        _oc_dst="$INSTALL_DIR/config/openclaw/openclaw.json"
        if [[ -f "$_oc_src" ]]; then
            if [[ ! "$_oc_src" -ef "$_oc_dst" ]]; then
                cp "$_oc_src" "$_oc_dst"
            fi
        elif [[ -f "$SCRIPT_DIR/config/openclaw/$OPENCLAW_CONFIG" ]]; then
            cp "$SCRIPT_DIR/config/openclaw/$OPENCLAW_CONFIG" "$_oc_dst"
        else
            error "Missing OpenClaw config $OPENCLAW_CONFIG and no fallback present in repo. This is a packaging bug; please re-clone or report."
        fi
        unset _oc_src _oc_dst
        # Resolve provider name/URL before any sed replacements that depend on them
        OPENCLAW_PROVIDER_NAME="${OPENCLAW_PROVIDER_NAME_DEFAULT}"
        OPENCLAW_PROVIDER_URL="${OPENCLAW_PROVIDER_URL_DEFAULT}"

        # Replace model and provider placeholders to match what the inference backend actually serves
        # Escape sed special chars in variable values to prevent injection
        _sed_escape() { printf '%s\n' "$1" | sed 's/[&/\|]/\\&/g'; }
        _oc_model_esc=$(_sed_escape "$OPENCLAW_MODEL")
        _oc_prov_esc=$(_sed_escape "$OPENCLAW_PROVIDER_NAME")
        _sed_i "s|__LLM_MODEL__|${_oc_model_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _sed_i "s|Qwen/Qwen2.5-[^\"]*|${_oc_model_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _sed_i "s|local-ollama|${_oc_prov_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _oc_key_esc=$(_sed_escape "${LITELLM_KEY:-none}")
        _sed_i "s|__LITELLM_KEY__|${_oc_key_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        log "Installed OpenClaw config: $OPENCLAW_CONFIG -> openclaw.json (model: $OPENCLAW_MODEL)"
        # Generate OPENCLAW_TOKEN (used by compose env and inject-token.js)
        OPENCLAW_TOKEN=$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p)
        # Note: inject-token.js regenerates /home/node/.openclaw/openclaw.json
        # on every container start, so that file stays ephemeral. OpenClaw also
        # writes agent, cron, and canvas state under /home/node/.openclaw; those
        # paths are bind-mounted under data/openclaw/home. workspace/ is
        # persisted separately under config/openclaw/workspace.
        mkdir -p "$INSTALL_DIR/data/openclaw/home"/{agents,canvas,cron}
        # Create workspace directory (must exist before Docker Compose,
        # otherwise Docker auto-creates it as root and the container can't write to it)
        mkdir -p "$INSTALL_DIR/config/openclaw/workspace/memory"
        # Copy workspace personality files (Todd identity, system knowledge, etc.)
        # Exclude .git and .openclaw dirs — those are runtime/dev artifacts
        if [[ -d "$SCRIPT_DIR/config/openclaw/workspace" ]]; then
            if command -v rsync &>/dev/null; then
                rsync -a --no-owner --no-group --exclude='.git' --exclude='.openclaw' --exclude='.gitkeep' \
                    "$SCRIPT_DIR/config/openclaw/workspace/" "$INSTALL_DIR/config/openclaw/workspace/"
            else
                cp -r "$SCRIPT_DIR/config/openclaw/workspace"/* "$INSTALL_DIR/config/openclaw/workspace/" 2>/dev/null || true
                rm -rf "$INSTALL_DIR/config/openclaw/workspace/.git" 2>/dev/null || true
                rm -rf "$INSTALL_DIR/config/openclaw/workspace/.openclaw" 2>/dev/null || true
            fi
            log "Installed OpenClaw workspace files (agent personality)"
        fi
        # OpenClaw container runs as node (uid 1000) — fix ownership
        # Pre-create data/openclaw so chown doesn't fail on a fresh install where
        # the directory hasn't been touched yet.
        mkdir -p "$INSTALL_DIR/data/openclaw"
        chown -R 1000:1000 "$INSTALL_DIR/data/openclaw" "$INSTALL_DIR/config/openclaw/workspace" || warn "Failed to chown openclaw paths to 1000:1000 (non-fatal); container may need uid fixup"
    fi

    # token-spy container runs as uid 1000 (baked in Dockerfile) — fix ownership
    chown -R 1000:1000 "$INSTALL_DIR/data/token-spy" || warn "Failed to chown data/token-spy to 1000:1000 (non-fatal); container may crash if installer ran as a different uid"

    # ── .env merge logic: preserve user-configured values on re-install ──
    dream_progress 40 "directories" "Generating secrets and configuration"
    # If an existing .env exists, read user-editable values so we don't
    # destroy API keys, custom ports, or manually-set secrets.
    _env_existing=""
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        _env_existing="$INSTALL_DIR/.env"
        log "Found existing .env — preserving user-configured values"
    fi

    # Safe reader: extract a value from existing .env without sourcing it
    _env_get() {
        local key="$1" default="${2:-}"
        if [[ -n "$_env_existing" ]]; then
            local val
            val=$(grep -m1 "^${key}=" "$_env_existing" 2>/dev/null | cut -d= -f2- || true)
            # Strip surrounding quotes
            val="${val%\"}" && val="${val#\"}"
            val="${val%\'}" && val="${val#\'}"
            if [[ -n "$val" ]]; then
                echo "$val"
                return
            fi
        fi
        echo "$default"
    }

    # Secrets: reuse existing values, generate only if missing
    WEBUI_SECRET=$(_env_get WEBUI_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    N8N_PASS=$(_env_get N8N_PASS "$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64)")
    LITELLM_KEY=$(_env_get LITELLM_KEY "sk-dream-$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LITELLM_LEMONADE_API_KEY=$(_env_get LITELLM_LEMONADE_API_KEY "sk-dream-lemonade-$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LIVEKIT_SECRET=$(_env_get LIVEKIT_API_SECRET "$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)")
    DASHBOARD_API_KEY=$(_env_get DASHBOARD_API_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    DREAM_AGENT_KEY=$(_env_get DREAM_AGENT_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    # HMAC key for signing dream-session cookies (magic-link redemption).
    # 32 random bytes hex-encoded. Rotating invalidates every issued cookie —
    # the only revocation mechanism we have today, so don't rotate casually.
    DREAM_SESSION_SECRET=$(_env_get DREAM_SESSION_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    SHIELD_API_KEY=$(_env_get SHIELD_API_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    DIFY_SECRET_KEY=$(_env_get DIFY_SECRET_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    QDRANT_API_KEY=$(_env_get QDRANT_API_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")
    OPENCODE_SERVER_PASSWORD=$(_env_get OPENCODE_SERVER_PASSWORD "$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64)")
    SEARXNG_SECRET=$(_env_get SEARXNG_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)")

    # Langfuse (LLM Observability). LANGFUSE_ENABLED mirrors the install-time
    # ENABLE_LANGFUSE toggle, falling back to whatever the user had in .env on
    # re-install so manual post-install `dream enable langfuse` edits survive.
    LANGFUSE_PORT=$(_env_get LANGFUSE_PORT "3006")
    LANGFUSE_ENABLED=$(_env_get LANGFUSE_ENABLED "${ENABLE_LANGFUSE:-false}")
    LANGFUSE_NEXTAUTH_SECRET=$(_env_get LANGFUSE_NEXTAUTH_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')")
    LANGFUSE_SALT=$(_env_get LANGFUSE_SALT "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')")
    LANGFUSE_ENCRYPTION_KEY=$(_env_get LANGFUSE_ENCRYPTION_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')")
    LANGFUSE_DB_PASSWORD=$(_env_get LANGFUSE_DB_PASSWORD "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_CLICKHOUSE_PASSWORD=$(_env_get LANGFUSE_CLICKHOUSE_PASSWORD "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_REDIS_PASSWORD=$(_env_get LANGFUSE_REDIS_PASSWORD "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_MINIO_ACCESS_KEY=$(_env_get LANGFUSE_MINIO_ACCESS_KEY "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_MINIO_SECRET_KEY=$(_env_get LANGFUSE_MINIO_SECRET_KEY "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')")
    LANGFUSE_PROJECT_PUBLIC_KEY=$(_env_get LANGFUSE_PROJECT_PUBLIC_KEY "pk-lf-dream-$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_PROJECT_SECRET_KEY=$(_env_get LANGFUSE_PROJECT_SECRET_KEY "sk-lf-dream-$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_INIT_PROJECT_ID=$(_env_get LANGFUSE_INIT_PROJECT_ID "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    LANGFUSE_INIT_USER_EMAIL=$(_env_get LANGFUSE_INIT_USER_EMAIL "admin@dreamserver.local")
    LANGFUSE_INIT_USER_PASSWORD=$(_env_get LANGFUSE_INIT_USER_PASSWORD "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
    MODEL_PROFILE_VALUE=$(_env_get MODEL_PROFILE "${MODEL_PROFILE_REQUESTED:-${MODEL_PROFILE:-qwen}}")

    _select_auto_cpu_value() {
        local key="$1" detected="$2"
        local existing
        existing=$(_env_get "$key" "")
        if [[ "$existing" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk "BEGIN { exit !($existing > 0 && $existing <= $detected) }"; then
            echo "$existing"
        else
            echo "$detected"
        fi
    }

    _cpu_backend="${GPU_BACKEND:-cpu}"
    [[ "$_cpu_backend" == "none" ]] && _cpu_backend="cpu"
    read -r _llama_cpu_limit_raw _llama_cpu_reservation_raw _docker_available_cpus <<< "$(calculate_llama_cpu_budget "$_cpu_backend")"
    _llama_cpu_limit_detected="${_llama_cpu_limit_raw}.0"
    _llama_cpu_reservation_detected="${_llama_cpu_reservation_raw}.0"
    LLAMA_CPU_LIMIT=$(_select_auto_cpu_value LLAMA_CPU_LIMIT "${_llama_cpu_limit_detected}")
    LLAMA_CPU_RESERVATION=$(_select_auto_cpu_value LLAMA_CPU_RESERVATION "${_llama_cpu_reservation_detected}")
    if awk "BEGIN { exit !($LLAMA_CPU_RESERVATION > $LLAMA_CPU_LIMIT) }"; then
        LLAMA_CPU_RESERVATION="$LLAMA_CPU_LIMIT"
    fi

    # Network binding (--lan sets 0.0.0.0; default is localhost-only)
    BIND_ADDRESS=$(_env_get BIND_ADDRESS "${BIND_ADDRESS:-127.0.0.1}")

    # Host LAN IP — only meaningful when BIND_ADDRESS=0.0.0.0. Some services
    # (e.g. openclaw) need to know the host's LAN address so the Control UI
    # accepts cross-origin requests from LAN clients. Detection prefers
    # `hostname -I` (GNU coreutils, Linux) then `ip route get` then ifconfig
    # so WSL2 + odd Linux variants are covered. Empty default keeps the
    # compose ${HOST_LAN_IP:-} fallback safe when binding to loopback.
    HOST_LAN_IP=""
    if [[ "$BIND_ADDRESS" == "0.0.0.0" ]]; then
        if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
            HOST_LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi
        if [[ -z "$HOST_LAN_IP" ]] && command -v ip >/dev/null 2>&1; then
            HOST_LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
        fi
        if [[ -z "$HOST_LAN_IP" ]] && command -v ifconfig >/dev/null 2>&1; then
            HOST_LAN_IP=$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')
        fi
    fi
    # Preserve operator override across re-runs: if .env already has a value,
    # use it instead of the freshly-detected one (matches the _env_get pattern
    # used for every other persistent value in this phase).
    HOST_LAN_IP=$(_env_get HOST_LAN_IP "$HOST_LAN_IP")

    # Whisper STT model — NVIDIA picks the larger turbo model, everyone else
    # uses base. Phase 12 reads this to pre-download the right file, and
    # Open WebUI reads it to request the same model for transcription.
    if [[ "$GPU_BACKEND" == "nvidia" ]]; then
        _default_stt_model="deepdml/faster-whisper-large-v3-turbo-ct2"
    else
        _default_stt_model="Systran/faster-whisper-base"
    fi
    AUDIO_STT_MODEL=$(_env_get AUDIO_STT_MODEL "${AUDIO_STT_MODEL:-$_default_stt_model}")

    # Preserve user-supplied cloud API keys
    ANTHROPIC_API_KEY=$(_env_get ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}")
    OPENAI_API_KEY=$(_env_get OPENAI_API_KEY "${OPENAI_API_KEY:-}")
    TOGETHER_API_KEY=$(_env_get TOGETHER_API_KEY "${TOGETHER_API_KEY:-}")
    MINIMAX_API_KEY=$(_env_get MINIMAX_API_KEY "${MINIMAX_API_KEY:-}")
    # Base64-encode GPU assignment JSON for safe .env storage
    if [[ -n "${GPU_ASSIGNMENT_JSON:-}" && "${GPU_ASSIGNMENT_JSON:-}" != "{}" ]]; then
        GPU_ASSIGNMENT_JSON_B64=$(echo "$GPU_ASSIGNMENT_JSON" | jq -c '.' | base64 -w0)
    else
        GPU_ASSIGNMENT_JSON_B64=""
    fi

    # Generate .env file
    # Subshell-scope a tighter umask so the file is created 0600 from the start
    # (closes a brief window on systems where $HOME is world-readable, e.g.
    # Ubuntu defaults). The umask MUST NOT leak to the rest of phase 06 or
    # subsequent phases — later mkdirs create container-bind-mount dirs that
    # need world-traverse (e.g. SearXNG runs as uid 977, OpenClaw as 1000).
    # The chmod 600 below is belt-and-braces.
    (
        umask 077
        cat > "$INSTALL_DIR/.env" << ENV_EOF
# Dream Server Configuration — ${TIER_NAME} Edition
# Generated by installer v${VERSION} on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Tier: ${TIER} (${TIER_NAME})

#=== Dream Server Version (used by dream-cli update for version-compat checks) ===
DREAM_VERSION=${VERSION:-2.4.0}

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with --lan or set manually)
BIND_ADDRESS=${BIND_ADDRESS}
# Host LAN IP (populated when BIND_ADDRESS=0.0.0.0; empty otherwise).
# Containers like openclaw read this to advertise the host's LAN address.
HOST_LAN_IP=${HOST_LAN_IP}

#=== LLM Backend Mode ===
DREAM_MODE=$(if [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "lemonade"; else echo "${DREAM_MODE:-local}"; fi)
LLM_API_URL=$(if [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "http://litellm:4000"; elif [[ "${DREAM_MODE:-local}" == "local" ]]; then echo "http://llama-server:8080"; else echo "http://litellm:4000"; fi)

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
TOGETHER_API_KEY=${TOGETHER_API_KEY:-}
MINIMAX_API_KEY=${MINIMAX_API_KEY:-}

#=== Service Auth (LiteLLM proxy) ===
TARGET_API_KEY=not-needed

#=== LLM Settings (llama-server) ===
MODEL_PROFILE=${MODEL_PROFILE_VALUE}
# Effective model profile for this hardware: ${MODEL_PROFILE_EFFECTIVE:-qwen}
LLM_MODEL=${LLM_MODEL}
GGUF_FILE=${GGUF_FILE}
MAX_CONTEXT=${MAX_CONTEXT}
CTX_SIZE=${MAX_CONTEXT}
GPU_BACKEND=${GPU_BACKEND}
N_GPU_LAYERS=${N_GPU_LAYERS:-99}
$(if [[ -n "${LLAMA_SERVER_IMAGE:-}" ]]; then echo "LLAMA_SERVER_IMAGE=${LLAMA_SERVER_IMAGE}"; fi)
$(if [[ -n "${LLAMA_SERVER_IMAGE_FALLBACK:-}" ]]; then echo "LLAMA_SERVER_IMAGE_FALLBACK=${LLAMA_SERVER_IMAGE_FALLBACK}"; fi)
#=== llama.cpp Runtime Tuning ===
LLAMA_ARG_FLASH_ATTN=${LLAMA_ARG_FLASH_ATTN:-auto}
LLAMA_ARG_CACHE_TYPE_K=${LLAMA_ARG_CACHE_TYPE_K:-f16}
LLAMA_ARG_CACHE_TYPE_V=${LLAMA_ARG_CACHE_TYPE_V:-f16}
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
LLAMA_CPU_LIMIT=${LLAMA_CPU_LIMIT}
LLAMA_CPU_RESERVATION=${LLAMA_CPU_RESERVATION}

$(if [[ "$GPU_BACKEND" == "amd" ]]; then cat << AMD_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== AMD ROCm Settings ===
HSA_OVERRIDE_GFX_VERSION=11.5.1
HSA_XNACK=1
ROCBLAS_USE_HIPBLASLT=1
AMDGPU_TARGET=gfx1151
LLAMA_CPP_REF=b8763

#=== LiteLLM → Lemonade outbound key (AMD only) ===
LITELLM_LEMONADE_API_KEY=${LITELLM_LEMONADE_API_KEY}
AMD_ENV
fi)
$(if [[ "$GPU_BACKEND" == "sycl" ]]; then cat << INTEL_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== Intel Arc / oneAPI SYCL Settings ===
ONEAPI_DEVICE_SELECTOR=level_zero:gpu
SYCL_CACHE_PERSISTENT=1
ZES_ENABLE_SYSMAN=1
INTEL_ENV
fi)

#=== Ports ===
OLLAMA_PORT=11434
WEBUI_PORT=3000
SEARXNG_PORT=8888
PERPLEXICA_PORT=3004
WHISPER_PORT=${WHISPER_PORT:-9000}
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
EMBEDDINGS_PORT=8090
LITELLM_PORT=4000
OPENCLAW_PORT=7860
LANGFUSE_PORT=${LANGFUSE_PORT}

#=== Hermes Agent ===
HERMES_LLM_BASE_URL=${HERMES_LLM_BASE_URL:-http://llama-server:8080/v1}
HERMES_LLM_API_KEY=${HERMES_LLM_API_KEY:-sk-dream-hermes-local}
HERMES_LANGUAGE=${HERMES_LANGUAGE:-en}
HERMES_PROXY_PORT=${HERMES_PROXY_PORT:-9120}
HERMES_PROXY_UPSTREAM=${HERMES_PROXY_UPSTREAM:-dream-hermes:9119}
DREAM_AUTH_UPSTREAM=${DREAM_AUTH_UPSTREAM:-dream-dashboard-api:3002}

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=${WEBUI_SECRET}
DASHBOARD_API_KEY=${DASHBOARD_API_KEY}
DREAM_AGENT_KEY=${DREAM_AGENT_KEY}
DREAM_SESSION_SECRET=${DREAM_SESSION_SECRET}
SHIELD_API_KEY=${SHIELD_API_KEY}
N8N_USER=admin@dreamserver.local
N8N_PASS=${N8N_PASS}
LITELLM_KEY=${LITELLM_KEY}
LIVEKIT_API_KEY=$(_env_get LIVEKIT_API_KEY "$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)")
LIVEKIT_API_SECRET=${LIVEKIT_SECRET}
OPENCLAW_TOKEN=${OPENCLAW_TOKEN:-$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p)}
QDRANT_API_KEY=${QDRANT_API_KEY}
OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD}
SEARXNG_SECRET=${SEARXNG_SECRET}
DIFY_SECRET_KEY=${DIFY_SECRET_KEY}

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper STT model passed to Open WebUI and pre-downloaded by Phase 12.
# Auto-selected based on GPU backend; edit to override.
AUDIO_STT_MODEL=${AUDIO_STT_MODEL}
TTS_VOICE=en_US-lessac-medium

#=== Web UI Settings ===
WEBUI_AUTH=true
ENABLE_WEB_SEARCH=true
WEB_SEARCH_ENGINE=searxng

#=== n8n Settings ===
N8N_HOST=localhost
N8N_WEBHOOK_URL=http://localhost:5678
TIMEZONE=${SYSTEM_TZ:-UTC}

#=== Langfuse (LLM Observability) ===
LANGFUSE_ENABLED=${LANGFUSE_ENABLED}
LANGFUSE_NEXTAUTH_SECRET=${LANGFUSE_NEXTAUTH_SECRET}
LANGFUSE_SALT=${LANGFUSE_SALT}
LANGFUSE_ENCRYPTION_KEY=${LANGFUSE_ENCRYPTION_KEY}
LANGFUSE_DB_PASSWORD=${LANGFUSE_DB_PASSWORD}
LANGFUSE_CLICKHOUSE_PASSWORD=${LANGFUSE_CLICKHOUSE_PASSWORD}
LANGFUSE_REDIS_PASSWORD=${LANGFUSE_REDIS_PASSWORD}
LANGFUSE_MINIO_ACCESS_KEY=${LANGFUSE_MINIO_ACCESS_KEY}
LANGFUSE_MINIO_SECRET_KEY=${LANGFUSE_MINIO_SECRET_KEY}
LANGFUSE_PROJECT_PUBLIC_KEY=${LANGFUSE_PROJECT_PUBLIC_KEY}
LANGFUSE_PROJECT_SECRET_KEY=${LANGFUSE_PROJECT_SECRET_KEY}
LANGFUSE_INIT_PROJECT_ID=${LANGFUSE_INIT_PROJECT_ID}
LANGFUSE_INIT_USER_EMAIL=${LANGFUSE_INIT_USER_EMAIL}
LANGFUSE_INIT_USER_PASSWORD=${LANGFUSE_INIT_USER_PASSWORD}

# ── Image Generation ──
ENABLE_IMAGE_GENERATION=${ENABLE_COMFYUI:-true}

#=== Multi-GPU Settings ===
GPU_COUNT=${GPU_COUNT:-1}
GPU_ASSIGNMENT_JSON_B64=${GPU_ASSIGNMENT_JSON_B64:-}
COMFYUI_GPU_UUID=${COMFYUI_GPU_UUID:-}
WHISPER_GPU_UUID=${WHISPER_GPU_UUID:-}
EMBEDDINGS_GPU_UUID=${EMBEDDINGS_GPU_UUID:-}
LLAMA_SERVER_GPU_UUIDS=${LLAMA_SERVER_GPU_UUIDS:-}
LLAMA_ARG_SPLIT_MODE=${LLAMA_ARG_SPLIT_MODE:-none}
LLAMA_ARG_TENSOR_SPLIT=${LLAMA_ARG_TENSOR_SPLIT:-}

ENV_EOF
    )

    chmod 600 "$INSTALL_DIR/.env"  # Secure secrets file
    ai_ok "Created $INSTALL_DIR"
    ai_ok "Generated secure secrets in .env (permissions: 600)"

    # Generate LiteLLM config for Lemonade.
    # Lemonade exposes models as "extra.<GGUF_FILENAME>" — the wildcard
    # passthrough (openai/*) does NOT work because it forwards the friendly
    # model name verbatim and lemonade returns 404.  Instead, map all
    # requests to the concrete model ID that lemonade actually serves.
    # bootstrap-upgrade.sh regenerates this config when the model swaps.
    if [[ "$GPU_BACKEND" == "amd" ]]; then
        mkdir -p "$INSTALL_DIR/config/litellm"
        # Source bootstrap-model.sh for BOOTSTRAP_GGUF_FILE and bootstrap_needed().
        # Pure library (zero side effects), all deps available by phase 06.
        # Phase 11 re-sources it harmlessly (idempotent).
        [[ -f "$SCRIPT_DIR/installers/lib/bootstrap-model.sh" ]] && . "$SCRIPT_DIR/installers/lib/bootstrap-model.sh"
        if type bootstrap_needed &>/dev/null && bootstrap_needed; then
            _active_gguf="$BOOTSTRAP_GGUF_FILE"
        else
            _active_gguf="$GGUF_FILE"
        fi
        cat > "$INSTALL_DIR/config/litellm/lemonade.yaml" << LITELLM_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/extra.${_active_gguf}
      api_base: http://llama-server:8080/api/v1
      api_key: ${LITELLM_LEMONADE_API_KEY}

  - model_name: "*"
    litellm_params:
      model: openai/extra.${_active_gguf}
      api_base: http://llama-server:8080/api/v1
      api_key: ${LITELLM_LEMONADE_API_KEY}

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 120
  stream_timeout: 60
LITELLM_EOF
        ai_ok "Generated LiteLLM config for Lemonade (model: extra.${_active_gguf})"
    fi

    # Validate generated .env against schema (fails fast on missing/unknown keys).
    dream_progress 41 "directories" "Validating configuration"
    if [[ -f "$SCRIPT_DIR/scripts/validate-env.sh" && -f "$SCRIPT_DIR/.env.schema.json" ]]; then
        if bash "$SCRIPT_DIR/scripts/validate-env.sh" "$INSTALL_DIR/.env" "$SCRIPT_DIR/.env.schema.json" >> "$LOG_FILE" 2>&1; then
            ai_ok "Validated .env against .env.schema.json"
        else
            error "Generated .env failed schema validation. See $LOG_FILE for details."
        fi
    else
        warn "Skipping .env schema validation (.env.schema.json or scripts/validate-env.sh missing)"
    fi

    # Generate SearXNG config with randomized secret key
    # Fix ownership from previous container runs (SearXNG writes as uid 977)
    mkdir -p "$INSTALL_DIR/config/searxng"
    if [[ -f "$INSTALL_DIR/config/searxng/settings.yml" ]] && ! [[ -w "$INSTALL_DIR/config/searxng/settings.yml" ]]; then
        sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR/config/searxng/settings.yml" 2>/dev/null || true
    fi
    cat > "$INSTALL_DIR/config/searxng/settings.yml" << SEARXNG_EOF
use_default_settings: true
server:
  secret_key: "${SEARXNG_SECRET}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
search:
  safe_search: 0
  formats:
    - html
    - json
engines:
  - name: duckduckgo
    disabled: false
  - name: google
    disabled: false
  - name: brave
    disabled: false
  - name: wikipedia
    disabled: false
  - name: github
    disabled: false
  - name: stackoverflow
    disabled: false
SEARXNG_EOF
    ai_ok "Generated SearXNG config with randomized secret key"
fi

# Documentation, CLI tools, and compose variants already copied by rsync/cp block above
