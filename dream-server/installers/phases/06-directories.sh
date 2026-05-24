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
#           DASHBOARD_API_KEY, SHIELD_API_KEY, TOKEN_SPY_API_KEY,
#           OPENCODE_SERVER_PASSWORD,
#           OPENCLAW_TOKEN, OPENCLAW_PROVIDER_NAME, OPENCLAW_PROVIDER_URL,
#           OPENCLAW_MODEL, OPENCLAW_CONTEXT, GPU_ASSIGNMENT_JSON_B64 (in .env)
#
# Modder notes:
#   This is the largest phase. Modify .env generation, add new config files,
#   or change directory layout here.
# ============================================================================

dream_progress 38 "directories" "Preparing installation directory"
chapter "SETTING UP INSTALLATION"

_phase06_step() {
    local step="$1"
    export INSTALL_PHASE="06-directories/${step}"
    log "Phase 06 step: ${step}"
}

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
    _phase06_step "create-directories"
    dream_progress 38 "directories" "Creating directory structure"
    mkdir -p "$INSTALL_DIR"/{config,data,models}
    mkdir -p "$INSTALL_DIR"/data/{open-webui,whisper,tts,n8n,qdrant,models,privacy-shield,ape,token-spy,hermes,persona}
    mkdir -p "$INSTALL_DIR"/data/hermes-proxy/{caddy-data,caddy-config}
    mkdir -p "$INSTALL_DIR"/data/langfuse/{postgres,clickhouse,redis,minio}
    mkdir -p "$INSTALL_DIR"/config/{n8n,litellm,openclaw,searxng}

    # Hermes runs its gateway/dashboard as the in-container `hermes` user
    # (uid 10000) and keeps HERMES_HOME at data/hermes mounted as /opt/data.
    # Upstream intentionally makes that directory 0700. A reinstall running
    # as the host user must not "repair" it back to uid 1000, or Hermes's web
    # status and Dream Talk JSON-RPC paths fail with PermissionError.
    if [[ "${ENABLE_HERMES:-false}" == "true" && -d "$INSTALL_DIR/data/hermes" ]]; then
        sudo chown -R 10000:10000 "$INSTALL_DIR/data/hermes" 2>/dev/null || \
            warn "Failed to restore data/hermes ownership to Hermes uid 10000 (Hermes dashboard may be unhealthy)"
        sudo chmod 700 "$INSTALL_DIR/data/hermes" 2>/dev/null || true
    fi

    # Fix ownership of data/config dirs that may have been created by containers
    # (e.g. SearXNG runs as uid 977, ComfyUI data owned by root)
    for _data_dir in "$INSTALL_DIR"/data/*/; do
        [[ "${ENABLE_HERMES:-false}" == "true" && "$_data_dir" == "$INSTALL_DIR/data/hermes/" ]] && continue
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
                [[ "${ENABLE_HERMES:-false}" == "true" && "$_d" == "$INSTALL_DIR/data/hermes/" ]] && continue
                [[ -d "$_d" ]] && ! [[ -w "$_d" ]] && _cant_write="$_cant_write ${_d#$INSTALL_DIR/}"
            done
        done
        if [[ -n "$_cant_write" ]]; then
            error "Cannot write to directories (likely container-owned):$_cant_write

Fix with: sudo chown -R \$(id -u):\$(id -g) $INSTALL_DIR/config $INSTALL_DIR/data — then re-run the installer."
        fi
    fi

    # Copy entire source tree to install dir (skip if same directory)
    _phase06_step "copy-source"
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
    _phase06_step "prune-retired-services"
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
    _phase06_step "copy-extensions-library"
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
    _phase06_step "configure-legacy-openclaw"
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
    _phase06_step "prepare-service-permissions"
    chown -R 1000:1000 "$INSTALL_DIR/data/token-spy" || warn "Failed to chown data/token-spy to 1000:1000 (non-fatal); container may crash if installer ran as a different uid"

    # ── .env merge logic: preserve user-configured values on re-install ──
    _phase06_step "generate-env"
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
    LEMONADE_EXTERNAL_VALUE="${LEMONADE_EXTERNAL:-false}"
    [[ "${LEMONADE_EXTERNAL_VALUE,,}" == "true" ]] && LEMONADE_EXTERNAL_VALUE="true" || LEMONADE_EXTERNAL_VALUE="false"
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" && -n "${LEMONADE_API_KEY:-}" ]]; then
        LITELLM_LEMONADE_API_KEY="$LEMONADE_API_KEY"
    fi
    LEMONADE_API_BASE_PATH_VALUE="$(_env_get LEMONADE_API_BASE_PATH "/api/v1")"
    [[ "$LEMONADE_API_BASE_PATH_VALUE" == /* ]] || LEMONADE_API_BASE_PATH_VALUE="/$LEMONADE_API_BASE_PATH_VALUE"
    LEMONADE_BASE_URL_VALUE=""
    LEMONADE_CONTAINER_BASE_URL_VALUE=""
    LEMONADE_PORT_VALUE=""
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        LEMONADE_BASE_URL_VALUE="$(_env_get LEMONADE_BASE_URL "${LEMONADE_BASE_URL:-http://localhost:13305}")"
        LEMONADE_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE%/}"
        for _lemonade_suffix in "/api/v1" "/v1" "/api"; do
            if [[ "$LEMONADE_BASE_URL_VALUE" == *"$_lemonade_suffix" ]]; then
                LEMONADE_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE%"$_lemonade_suffix"}"
            fi
        done
        LEMONADE_PORT_VALUE="${AMD_INFERENCE_PORT:-}"
        if [[ -z "$LEMONADE_PORT_VALUE" ]]; then
            if [[ "$LEMONADE_BASE_URL_VALUE" =~ ^https?://[^/:]+:([0-9]+)(/|$) ]]; then
                LEMONADE_PORT_VALUE="${BASH_REMATCH[1]}"
            else
                LEMONADE_PORT_VALUE="13305"
            fi
        fi
        LEMONADE_CONTAINER_BASE_URL_VALUE="$(_env_get LEMONADE_CONTAINER_BASE_URL "")"
        if [[ -z "$LEMONADE_CONTAINER_BASE_URL_VALUE" ]]; then
            case "$LEMONADE_BASE_URL_VALUE" in
                http://localhost:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/localhost:/http:\/\/host.docker.internal:}" ;;
                http://127.0.0.1:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/127.0.0.1:/http:\/\/host.docker.internal:}" ;;
                http://[::1]:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/[::1]:/http:\/\/host.docker.internal:}" ;;
                *) LEMONADE_CONTAINER_BASE_URL_VALUE="$LEMONADE_BASE_URL_VALUE" ;;
            esac
        fi
        LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_CONTAINER_BASE_URL_VALUE%/}"
    fi
    LEMONADE_API_BASE_VALUE="${LEMONADE_BASE_URL_VALUE}${LEMONADE_API_BASE_PATH_VALUE}"
    LEMONADE_CONTAINER_API_BASE_VALUE="$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_CONTAINER_BASE_URL_VALUE}${LEMONADE_API_BASE_PATH_VALUE}"; else echo "http://llama-server:8080/api/v1"; fi)"
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
    _token_spy_key_default=""
    if [[ -f "$INSTALL_DIR/data/token-spy/token-spy-api-key.txt" ]]; then
        _token_spy_key_default=$(tr -d '\r\n' < "$INSTALL_DIR/data/token-spy/token-spy-api-key.txt" 2>/dev/null || true)
    fi
    if [[ -z "$_token_spy_key_default" ]]; then
        _token_spy_key_default=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)
    fi
    TOKEN_SPY_API_KEY=$(_env_get TOKEN_SPY_API_KEY "$_token_spy_key_default")
    unset _token_spy_key_default
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
    DREAM_MODE_VALUE="$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "lemonade"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "lemonade"; else echo "${DREAM_MODE:-local}"; fi)"
    _default_llm_api_url="$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "http://litellm:4000"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "http://litellm:4000"; elif [[ "${DREAM_MODE:-local}" == "local" ]]; then echo "http://llama-server:8080"; else echo "http://litellm:4000"; fi)"
    LLM_API_URL_VALUE=$(_env_get LLM_API_URL "$_default_llm_api_url")
    if [[ "${DREAM_MODE:-local}" == "cloud" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
    elif [[ "$GPU_BACKEND" == "amd" || "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
    else
        _default_hermes_base_url="http://llama-server:8080/v1"
        _default_hermes_api_key="sk-dream-hermes-local"
    fi
    HERMES_LLM_BASE_URL_VALUE=$(_env_get HERMES_LLM_BASE_URL "$_default_hermes_base_url")
    HERMES_LLM_API_KEY_VALUE=$(_env_get HERMES_LLM_API_KEY "$_default_hermes_api_key")
    LLM_API_URL="$LLM_API_URL_VALUE"
    HERMES_LLM_BASE_URL="$HERMES_LLM_BASE_URL_VALUE"
    HERMES_LLM_API_KEY="$HERMES_LLM_API_KEY_VALUE"
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" && "$LEMONADE_BASE_URL_VALUE" =~ ^http://(localhost|127\.0\.0\.1|\[::1\])(:|/|$) && "$(uname -s 2>/dev/null || echo unknown)" == "Linux" ]]; then
        warn "Existing Lemonade URL uses loopback ($LEMONADE_BASE_URL_VALUE). Docker containers will use $LEMONADE_CONTAINER_BASE_URL_VALUE; ensure Lemonade is reachable there (for example: lemonade config set host=0.0.0.0 on a trusted host)."
    fi

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

    _cap_cpu_value() {
        local desired="$1" ceiling="$2"
        awk -v desired="$desired" -v ceiling="$ceiling" '
            BEGIN {
                if (ceiling <= 0) ceiling = 1
                value = desired
                if (value > ceiling) value = ceiling
                if (value < 0.01) value = 0.01
                printf "%.1f", value
            }'
    }

    _select_service_cpu_limit() {
        local key="$1" desired="$2" available="$3"
        _select_auto_cpu_value "$key" "$(_cap_cpu_value "$desired" "$available")"
    }

    _select_service_cpu_reservation() {
        local key="$1" desired="$2" limit="$3"
        _select_auto_cpu_value "$key" "$(_cap_cpu_value "$desired" "$limit")"
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

    TTS_CPU_LIMIT=$(_select_service_cpu_limit TTS_CPU_LIMIT "8.0" "$_docker_available_cpus")
    TTS_CPU_RESERVATION=$(_select_service_cpu_reservation TTS_CPU_RESERVATION "2.0" "$TTS_CPU_LIMIT")
    WHISPER_CPU_LIMIT=$(_select_service_cpu_limit WHISPER_CPU_LIMIT "4.0" "$_docker_available_cpus")
    WHISPER_CPU_RESERVATION=$(_select_service_cpu_reservation WHISPER_CPU_RESERVATION "1.0" "$WHISPER_CPU_LIMIT")
    HERMES_CPU_LIMIT=$(_select_service_cpu_limit HERMES_CPU_LIMIT "4.0" "$_docker_available_cpus")
    HERMES_CPU_RESERVATION=$(_select_service_cpu_reservation HERMES_CPU_RESERVATION "0.5" "$HERMES_CPU_LIMIT")
    COMFYUI_CPU_LIMIT=$(_select_service_cpu_limit COMFYUI_CPU_LIMIT "16.0" "$_docker_available_cpus")
    COMFYUI_CPU_RESERVATION=$(_select_service_cpu_reservation COMFYUI_CPU_RESERVATION "2.0" "$COMFYUI_CPU_LIMIT")

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

    # Device name — used by dream-mdns (publishes <name>.local + per-service
    # subdomains: auth.<name>.local, chat.<name>.local, etc.) and by magic-
    # link URL generation in dashboard-api. The previous default literal
    # "dream" causes mDNS NonUniqueNameException collisions when more than
    # one Dream Server install is on the same LAN: the first one wins and
    # every subsequent host's mDNS service crash-loops, so phones following
    # invite QR codes from the losing hosts land on the winning host (or
    # nothing at all). Auto-derive from the system hostname for per-host
    # uniqueness, sanitized to match .env.schema.json's pattern
    # (^[a-zA-Z0-9][a-zA-Z0-9-]{0,30}[a-zA-Z0-9]$|^[a-zA-Z0-9]$). Fall back
    # to "dream" only when the hostname can't be sanitized into the schema.
    _device_default="dream"
    if command -v hostname >/dev/null 2>&1; then
        _raw_hn="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
        # Lowercase; collapse any non-[a-z0-9-] to a single '-'; trim
        # leading/trailing '-'; cap at 32 chars.
        _hn="$(printf '%s' "$_raw_hn" \
            | tr '[:upper:]' '[:lower:]' \
            | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//' \
            | cut -c1-32 \
            | sed -E 's/-+$//')"
        # Schema requires first + last char alphanumeric.
        if [[ -n "$_hn" && "$_hn" =~ ^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$ ]]; then
            _device_default="$_hn"
        fi
    fi
    DREAM_DEVICE_NAME=$(_env_get DREAM_DEVICE_NAME "$_device_default")

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
DREAM_VERSION=${VERSION:-2.5.0}

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with --lan or set manually)
BIND_ADDRESS=${BIND_ADDRESS}
# Host LAN IP (populated when BIND_ADDRESS=0.0.0.0; empty otherwise).
# Containers like openclaw read this to advertise the host's LAN address.
HOST_LAN_IP=${HOST_LAN_IP}

#=== LLM Backend Mode ===
DREAM_MODE=${DREAM_MODE_VALUE}
LLM_API_URL=${LLM_API_URL_VALUE}
LLM_BACKEND=$(if [[ "$DREAM_MODE_VALUE" == "lemonade" ]]; then echo "lemonade"; else echo "llama-server"; fi)
LLM_API_BASE_PATH=$(if [[ "$DREAM_MODE_VALUE" == "lemonade" ]]; then echo "${LEMONADE_API_BASE_PATH_VALUE}"; else echo "/v1"; fi)
AMD_INFERENCE_RUNTIME=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" || ( "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ) ]]; then echo "lemonade"; else echo ""; fi)
AMD_INFERENCE_BACKEND=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${AMD_INFERENCE_BACKEND:-auto}"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_LINUX_BACKEND:-rocm}"; else echo ""; fi)
AMD_INFERENCE_LOCATION=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "host"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "container"; else echo ""; fi)
AMD_INFERENCE_PORT=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_PORT_VALUE}"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_API_PORT:-8080}"; else echo ""; fi)
AMD_INFERENCE_SUPPORTED_BACKENDS=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${AMD_INFERENCE_SUPPORTED_BACKENDS:-auto}"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_LINUX_BACKEND:-rocm}"; else echo ""; fi)
AMD_INFERENCE_RUNTIME_MODE=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "external-lemonade"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "linux-container"; else echo ""; fi)
AMD_INFERENCE_MANAGED=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "false"; elif [[ "$GPU_BACKEND" == "amd" && "${DREAM_MODE:-local}" == "local" ]]; then echo "true"; else echo ""; fi)
LEMONADE_EXTERNAL=${LEMONADE_EXTERNAL_VALUE}
LEMONADE_BASE_URL=${LEMONADE_BASE_URL_VALUE}
LEMONADE_CONTAINER_BASE_URL=${LEMONADE_CONTAINER_BASE_URL_VALUE}
LEMONADE_API_BASE_PATH=${LEMONADE_API_BASE_PATH_VALUE}
LEMONADE_MODEL=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_MODEL:-${LLM_MODEL_VALUE}}"; else echo "${LEMONADE_MODEL:-}"; fi)

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
MODEL_RECOMMENDED_MODEL=${LLM_MODEL}
MODEL_RECOMMENDED_GGUF=${GGUF_FILE}
MODEL_RECOMMENDED_CONTEXT=${MAX_CONTEXT}
MODEL_RECOMMENDATION_SOURCE=${MODEL_RECOMMENDATION_SOURCE:-installer_tier_map}
MODEL_RECOMMENDATION_POLICY=${MODEL_RECOMMENDATION_POLICY:-tier-map}
MODEL_RECOMMENDATION_CONFIDENCE=${MODEL_RECOMMENDATION_CONFIDENCE:-medium}
MODEL_RECOMMENDATION_REASON=${MODEL_RECOMMENDATION_REASON:-Selected by installer tier ${TIER} (${TIER_NAME}) for ${GPU_BACKEND} backend; benchmark locally after first launch.}
MODEL_RECOMMENDED_ALTERNATIVES=${MODEL_RECOMMENDED_ALTERNATIVES:-}
MODEL_PERFORMANCE_SOURCE=benchmark_required
MODEL_PERFORMANCE_LABEL=Benchmark after first launch
GPU_BACKEND=${GPU_BACKEND}
SYSTEM_RAM_GB=${RAM_GB:-0}
N_GPU_LAYERS=${N_GPU_LAYERS:-99}
$(if [[ -n "${LLAMA_SERVER_IMAGE:-}" ]]; then echo "LLAMA_SERVER_IMAGE=${LLAMA_SERVER_IMAGE}"; fi)
$(if [[ -n "${LLAMA_SERVER_IMAGE_FALLBACK:-}" ]]; then echo "LLAMA_SERVER_IMAGE_FALLBACK=${LLAMA_SERVER_IMAGE_FALLBACK}"; fi)
#=== llama.cpp Runtime Tuning ===
LLAMA_ARG_FLASH_ATTN=${LLAMA_ARG_FLASH_ATTN:-auto}
LLAMA_ARG_CACHE_TYPE_K=${LLAMA_ARG_CACHE_TYPE_K:-f16}
LLAMA_ARG_CACHE_TYPE_V=${LLAMA_ARG_CACHE_TYPE_V:-f16}
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
$(if [[ -n "${LLAMA_ARG_N_CPU_MOE:-}" ]]; then echo "LLAMA_ARG_N_CPU_MOE=${LLAMA_ARG_N_CPU_MOE}"; fi)
$(if [[ -n "${LLAMA_ARG_NO_CACHE_PROMPT:-}" ]]; then echo "LLAMA_ARG_NO_CACHE_PROMPT=${LLAMA_ARG_NO_CACHE_PROMPT}"; fi)
$(if [[ -n "${LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS:-}" ]]; then echo "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=${LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS}"; fi)
LLAMA_PARALLEL=${LLAMA_PARALLEL:-1}
# Optional MTP speculative decoding only. Requires an MTP-capable GGUF and llama.cpp build.
# LLAMA_ARG_SPEC_TYPE=draft-mtp
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3
LLAMA_CPU_LIMIT=${LLAMA_CPU_LIMIT}
LLAMA_CPU_RESERVATION=${LLAMA_CPU_RESERVATION}

# Bundled service CPU budgets. These are capped to CPUs exposed by Docker so
# small hosts do not fail container creation on fixed compose limits.
TTS_CPU_LIMIT=${TTS_CPU_LIMIT}
TTS_CPU_RESERVATION=${TTS_CPU_RESERVATION}
WHISPER_CPU_LIMIT=${WHISPER_CPU_LIMIT}
WHISPER_CPU_RESERVATION=${WHISPER_CPU_RESERVATION}
HERMES_CPU_LIMIT=${HERMES_CPU_LIMIT}
HERMES_CPU_RESERVATION=${HERMES_CPU_RESERVATION}
COMFYUI_CPU_LIMIT=${COMFYUI_CPU_LIMIT}
COMFYUI_CPU_RESERVATION=${COMFYUI_CPU_RESERVATION}

$(if [[ "$GPU_BACKEND" == "amd" ]]; then
    # Read gfx target from topology detection. Falls back to gfx1151 (Strix Halo)
    # if the topology probe failed — preserves prior behavior for the OG target.
    _amd_gfx_detected=$(echo "${GPU_TOPOLOGY_JSON:-{\}}" | jq -r '[.gpus[]?.gfx_version] | unique | .[0] // "gfx1151"' 2>/dev/null || echo "gfx1151")
    [[ -z "$_amd_gfx_detected" || "$_amd_gfx_detected" == "null" || "$_amd_gfx_detected" == "unknown" ]] && _amd_gfx_detected="gfx1151"

    # HSA_OVERRIDE_GFX_VERSION is a Strix Halo (gfx1151) workaround — that target
    # is not in ROCm 7.x's official support list, so we coerce HSA to load
    # gfx1151 kernels by reporting "11.5.1". For natively-supported parts
    # (gfx942 / MI300X, gfx90a / MI250, gfx1100 / RX 7900, etc.) we MUST NOT set
    # this — doing so reports the wrong ISA and triggers
    # HSA_STATUS_ERROR_INVALID_ISA at model-load.
    case "$_amd_gfx_detected" in
        gfx1151) _amd_hsa_override="HSA_OVERRIDE_GFX_VERSION=11.5.1" ;;
        *)       _amd_hsa_override="# HSA_OVERRIDE_GFX_VERSION unset — $_amd_gfx_detected is natively supported" ;;
    esac

    # The custom llama-server binary at /opt/llama-custom is built with Strix
    # Halo-specific patches (MMQ tile size reduced from 64 to 48 for gfx1151's
    # register file). Pointing Lemonade at it from any other architecture either
    # ISA-faults (kernels compiled for gfx1151) or runs a perf-regressed binary.
    # Only opt-in when the host is actually Strix Halo; otherwise leave unset so
    # docker-compose.amd.yml's empty default lets Lemonade use its bundled
    # ROCm-aware binary.
    if [[ "$_amd_gfx_detected" == "gfx1151" ]]; then
        _amd_custom_bin="LEMONADE_LLAMACPP_ROCM_BIN=/opt/llama-custom/llama-server"
    else
        _amd_custom_bin="# LEMONADE_LLAMACPP_ROCM_BIN unset — custom binary is gfx1151-only; Lemonade uses bundled binary on $_amd_gfx_detected"
    fi

    cat << AMD_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== AMD ROCm Settings (gfx target detected from topology) ===
LEMONADE_SERVER_IMAGE=${LEMONADE_SERVER_IMAGE:-${BACKEND_LEMONADE_CONTAINER_IMAGE:-ghcr.io/lemonade-sdk/lemonade-server:v10.2.0}}
${_amd_hsa_override}
HSA_XNACK=1
ROCBLAS_USE_HIPBLASLT=1
AMDGPU_TARGET=${_amd_gfx_detected}
LLAMA_CPP_REF=b8763
${_amd_custom_bin}

#=== LiteLLM → Lemonade outbound key (AMD only) ===
LITELLM_LEMONADE_API_KEY=${LITELLM_LEMONADE_API_KEY}
AMD_ENV
    unset _amd_gfx_detected _amd_hsa_override _amd_custom_bin
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
# On AMD/Lemonade hosts, route Hermes through litellm. Lemonade is strict
# about model names (returns 404 if model field doesn't exactly match the
# loaded gguf) and drops concurrent connections during multi-step agent
# loops (web_search → reason → tool result → reason …) with
# APIConnectionError. litellm wraps with "*" wildcard normalization +
# retry logic, hiding both bumps. On non-AMD installs, talk direct to
# llama-server (native llama.cpp tolerates any model field).
HERMES_LLM_BASE_URL=${HERMES_LLM_BASE_URL_VALUE}
HERMES_LLM_API_KEY=${HERMES_LLM_API_KEY_VALUE}
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
TOKEN_SPY_API_KEY=${TOKEN_SPY_API_KEY}
OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD}
SEARXNG_SECRET=${SEARXNG_SECRET}
DIFY_SECRET_KEY=${DIFY_SECRET_KEY}

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper STT model passed to Open WebUI and pre-downloaded by Phase 12.
# Auto-selected based on GPU backend; edit to override.
AUDIO_STT_MODEL=${AUDIO_STT_MODEL}
TTS_VOICE=en_US-lessac-medium

#=== Device Name / mDNS / Proxy hostnames ===
# Used by dream-mdns to publish <name>.local on the LAN, by dream-proxy
# to route auth/chat/dashboard subdomains, and by dashboard-api when
# generating magic-link invite URLs. Auto-derived from the system
# hostname at install time so multiple Dream Server installs on the
# same LAN don't collide on a shared default name. Override by editing
# this line and restarting dream-mdns + dream-proxy.
DREAM_DEVICE_NAME=${DREAM_DEVICE_NAME}

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
$(if [[ "$GPU_BACKEND" == "amd" && "${GPU_COUNT:-1}" -gt 1 ]]; then cat << AMD_MULTI_ENV

#=== AMD Multi-GPU Settings ===
LLAMA_SERVER_GPU_INDICES=${LLAMA_SERVER_GPU_INDICES:-}
COMFYUI_GPU_INDEX=${COMFYUI_GPU_INDEX:-0}
WHISPER_GPU_INDEX=${WHISPER_GPU_INDEX:-0}
EMBEDDINGS_GPU_INDEX=${EMBEDDINGS_GPU_INDEX:-0}
AMD_MULTI_ENV
fi)

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
    if [[ "$GPU_BACKEND" == "amd" || "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        _phase06_step "render-amd-litellm-config"
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
        _lemonade_model_id=""
        if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
            _lemonade_model_id="${LEMONADE_MODEL:-${LLM_MODEL_VALUE:-default}}"
        fi
        # Pass chat_template_kwargs.enable_thinking=false to Lemonade so Qwen3
        # thinking-mode is OFF by default for every client. Perplexica and any
        # other consumer that doesn't manually `/no_think` its prompts would
        # otherwise hang for many minutes per request — the model emits a
        # <think>...</think> block that, on a long synthesis prompt, can run
        # past the client timeout before any visible token reaches the UI.
        # This kwarg is a Qwen3-specific switch and is safely ignored by
        # non-Qwen3 chat templates, so it's safe to bake into the default
        # lemonade config regardless of which model the install resolves to.
        # bootstrap-upgrade.sh mirrors this when it regenerates the file
        # after a hot-swap.
        _renderer_ok=false
        _renderer_py="${DREAM_PYTHON_CMD:-}"
        if [[ -z "$_renderer_py" && -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            _renderer_py="$(ds_detect_python_cmd 2>/dev/null || true)"
        fi
        if [[ -z "$_renderer_py" ]]; then
            _renderer_py="python3"
        fi
        if [[ -f "$SCRIPT_DIR/scripts/render-runtime-configs.py" ]] && command -v "$_renderer_py" >/dev/null 2>&1; then
            if "$_renderer_py" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
                --surface litellm-lemonade \
                --dream-mode lemonade \
                --gpu-backend amd \
                --gguf-file "$_active_gguf" \
                --lemonade-model-id "$_lemonade_model_id" \
                --lemonade-api-base "$LEMONADE_CONTAINER_API_BASE_VALUE" \
                --litellm-key "$LITELLM_LEMONADE_API_KEY" \
                --output-root "$INSTALL_DIR" \
                --write >> "$LOG_FILE" 2>&1; then
                _renderer_ok=true
            else
                warn "Runtime config renderer failed for Lemonade; falling back to inline writer"
            fi
        fi
        if [[ "$_renderer_ok" != "true" ]]; then
            cat > "$INSTALL_DIR/config/litellm/lemonade.yaml" << LITELLM_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/$(if [[ -n "$_lemonade_model_id" ]]; then echo "$_lemonade_model_id"; else echo "extra.${_active_gguf}"; fi)
      api_base: ${LEMONADE_CONTAINER_API_BASE_VALUE}
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/$(if [[ -n "$_lemonade_model_id" ]]; then echo "$_lemonade_model_id"; else echo "extra.${_active_gguf}"; fi)
      api_base: ${LEMONADE_CONTAINER_API_BASE_VALUE}
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 120
  stream_timeout: 60
LITELLM_EOF
        fi
        if [[ -n "$_lemonade_model_id" ]]; then
            ai_ok "Generated LiteLLM config for external Lemonade (model: ${_lemonade_model_id})"
        else
            ai_ok "Generated LiteLLM config for Lemonade (model: extra.${_active_gguf})"
        fi
        unset _renderer_ok _renderer_py
    fi

    # Validate generated .env against schema (fails fast on missing/unknown keys).
    _phase06_step "validate-env"
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
    _phase06_step "generate-searxng-config"
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
