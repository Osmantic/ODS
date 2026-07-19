#!/bin/bash
# ============================================================================
# ODS macOS Installer -- Environment Generator
# ============================================================================
# Part of: installers/macos/lib/
# Purpose: Generate .env file, SearXNG config, OpenClaw configs
#          Uses /dev/urandom + openssl for secrets
#
# Canonical source: installers/phases/06-directories.sh (keep .env format in sync)
#
# Modder notes:
#   Modify generate_ods_env to add new environment variables.
#   All secrets use cryptographic RNG -- never use $RANDOM for secrets.
# ============================================================================

# Generate cryptographically secure hex string
new_secure_hex() {
    local bytes="${1:-32}"
    openssl rand -hex "$bytes" 2>/dev/null || \
        head -c "$bytes" /dev/urandom | xxd -p | tr -d '\n'
}

# Generate cryptographically secure base64 string
new_secure_base64() {
    local bytes="${1:-32}"
    openssl rand -base64 "$bytes" 2>/dev/null | tr -d '\n' || \
        head -c "$bytes" /dev/urandom | base64 | tr -d '\n'
}

# Read a KEY=VALUE pair from an existing .env file.
# Arguments:
#   1) env_path: full path to .env
#   2) key: environment variable name (e.g., DASHBOARD_API_KEY)
# Output: the value (without quotes), or empty string if not found.
read_env_value() {
    local env_path="$1"
    local key="$2"
    [[ -f "$env_path" ]] || { echo ""; return 0; }
    grep -E "^${key}=" "$env_path" 2>/dev/null | sed -n '1p' | cut -d'=' -f2- | tr -d '\r' || true
}

read_token_spy_api_key() {
    local install_dir="$1"
    local token_spy_key_path="${install_dir}/data/token-spy/token-spy-api-key.txt"
    [[ -f "$token_spy_key_path" ]] || { echo ""; return 0; }
    tr -d '\r\n' < "$token_spy_key_path" 2>/dev/null || true
}

# Read SearXNG secret_key from an existing settings.yml file.
# Arguments:
#   1) settings_path: full path to settings.yml
# Output: the secret_key value, or empty string if not found.
read_searxng_secret() {
    local settings_path="$1"
    [[ -f "$settings_path" ]] || { echo ""; return 0; }
    # Expected line format: secret_key: "...."
    grep -E '^[[:space:]]*secret_key:[[:space:]]*"' "$settings_path" 2>/dev/null \
        | sed -n '1p' \
        | sed -E 's/^[[:space:]]*secret_key:[[:space:]]*"([^"]+)".*$/\1/' \
        | tr -d '\r' || true
}

upsert_env_value() {
    local env_path="$1"
    local key="$2"
    local value="$3"
    if grep -qE "^${key}=" "$env_path" 2>/dev/null; then
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$env_path"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_path"
    fi
}

cap_cpu_value() {
    local desired="$1" ceiling="$2"
    LC_ALL=C awk -v desired="$desired" -v ceiling="$ceiling" '
        BEGIN {
            if (ceiling <= 0) ceiling = 1
            value = desired
            if (value > ceiling) value = ceiling
            if (value < 0.01) value = 0.01
            printf "%.1f", value
        }'
}

select_auto_cpu_value() {
    local existing="$1" detected="$2"
    if [[ "$existing" =~ ^[0-9]+([.][0-9]+)?$ ]] && LC_ALL=C awk "BEGIN { exit !($existing > 0 && $existing <= $detected) }"; then
        echo "$existing"
    else
        echo "$detected"
    fi
}

select_env_service_cpu_limit() {
    local env_path="$1" key="$2" desired="$3" available="$4"
    select_auto_cpu_value "$(read_env_value "$env_path" "$key")" "$(cap_cpu_value "$desired" "$available")"
}

select_env_service_cpu_reservation() {
    local env_path="$1" key="$2" desired="$3" limit="$4"
    select_auto_cpu_value "$(read_env_value "$env_path" "$key")" "$(cap_cpu_value "$desired" "$limit")"
}

# Detect the host's LAN IP. Used to populate HOST_LAN_IP when the operator
# has set BIND_ADDRESS=0.0.0.0 (macOS has no --lan flag; this is opt-in via
# manual .env edit). Returns empty string when no non-loopback address can
# be found. BSD-safe: macOS lacks `hostname -I`, so we probe ifconfig first.
detect_host_lan_ip() {
    local ip=""
    if command -v ifconfig >/dev/null 2>&1; then
        ip=$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')
    fi
    if [[ -z "$ip" ]] && command -v ip >/dev/null 2>&1; then
        ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
    fi
    if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    printf '%s\n' "$ip"
}

sanitize_device_name() {
    local raw="${1:-}"
    local name
    name="$(printf '%s' "$raw" \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//' \
        | cut -c1-32 \
        | sed -E 's/-+$//')"
    if [[ -n "$name" && "$name" =~ ^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$ ]]; then
        printf '%s\n' "$name"
    else
        printf 'ods\n'
    fi
}

detect_device_name() {
    local raw=""
    # macOS users often set LocalHostName for Bonjour sharing; prefer it
    # because it already represents the host's LAN identity.
    if command -v scutil >/dev/null 2>&1; then
        raw="$(scutil --get LocalHostName 2>/dev/null || true)"
    fi
    if [[ -z "$raw" ]] && command -v hostname >/dev/null 2>&1; then
        raw="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
    fi
    sanitize_device_name "$raw"
}

# Detect system timezone (macOS-specific)
detect_timezone() {
    local tz=""
    # macOS: read from systemsetup or /etc/localtime symlink
    tz=$(systemsetup -gettimezone 2>/dev/null | awk -F': ' '{print $2}')
    if [[ -z "$tz" ]] && [[ -L /etc/localtime ]]; then
        tz=$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')
    fi
    echo "${tz:-UTC}"
}

generate_ods_env() {
    local install_dir="$1"
    local tier="$2"
    local force_overwrite="${3:-false}"

    local env_path="${install_dir}/.env"
    local searx_settings_path="${install_dir}/config/searxng/settings.yml"
    local cpu_limit_raw cpu_reservation_raw docker_available_cpus
    local detected_cpu_limit detected_cpu_reservation
    local tts_cpu_limit tts_cpu_reservation whisper_cpu_limit whisper_cpu_reservation
    local hermes_cpu_limit hermes_cpu_reservation comfyui_cpu_limit comfyui_cpu_reservation
    read -r cpu_limit_raw cpu_reservation_raw docker_available_cpus <<< "$(calculate_llama_cpu_budget "apple")"
    detected_cpu_limit="${cpu_limit_raw}.0"
    detected_cpu_reservation="${cpu_reservation_raw}.0"

    # Idempotency: preserve existing .env (and secrets) unless --force was provided.
    if [[ -f "$env_path" ]] && [[ "$force_overwrite" != "true" ]]; then
        ENV_DASHBOARD_KEY="$(read_env_value "$env_path" "DASHBOARD_API_KEY")"
        ENV_OPENCLAW_TOKEN="$(read_env_value "$env_path" "OPENCLAW_TOKEN")"

        # SearXNG secret: prefer .env, fall back to settings.yml, then generate.
        ENV_SEARXNG_SECRET="$(read_env_value "$env_path" "SEARXNG_SECRET")"
        if [[ -z "$ENV_SEARXNG_SECRET" ]]; then
            ENV_SEARXNG_SECRET="$(read_searxng_secret "$searx_settings_path")"
        fi
        if [[ -z "$ENV_SEARXNG_SECRET" ]]; then
            ENV_SEARXNG_SECRET="$(new_secure_hex 32)"
        fi

        local existing_limit existing_reservation
        existing_limit="$(read_env_value "$env_path" "LLAMA_CPU_LIMIT")"
        existing_reservation="$(read_env_value "$env_path" "LLAMA_CPU_RESERVATION")"
        if [[ "$existing_limit" =~ ^[0-9]+([.][0-9]+)?$ ]] && LC_ALL=C awk "BEGIN { exit !($existing_limit > 0 && $existing_limit <= $detected_cpu_limit) }"; then
            detected_cpu_limit="$existing_limit"
        fi
        if [[ "$existing_reservation" =~ ^[0-9]+([.][0-9]+)?$ ]] && LC_ALL=C awk "BEGIN { exit !($existing_reservation > 0 && $existing_reservation <= $detected_cpu_reservation) }"; then
            detected_cpu_reservation="$existing_reservation"
        fi
        if LC_ALL=C awk "BEGIN { exit !($detected_cpu_reservation > $detected_cpu_limit) }"; then
            detected_cpu_reservation="$detected_cpu_limit"
        fi
        upsert_env_value "$env_path" "LLAMA_CPU_LIMIT" "$detected_cpu_limit"
        upsert_env_value "$env_path" "LLAMA_CPU_RESERVATION" "$detected_cpu_reservation"
        tts_cpu_limit="$(select_env_service_cpu_limit "$env_path" "TTS_CPU_LIMIT" "8.0" "$docker_available_cpus")"
        tts_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "TTS_CPU_RESERVATION" "2.0" "$tts_cpu_limit")"
        whisper_cpu_limit="$(select_env_service_cpu_limit "$env_path" "WHISPER_CPU_LIMIT" "4.0" "$docker_available_cpus")"
        whisper_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "WHISPER_CPU_RESERVATION" "1.0" "$whisper_cpu_limit")"
        hermes_cpu_limit="$(select_env_service_cpu_limit "$env_path" "HERMES_CPU_LIMIT" "4.0" "$docker_available_cpus")"
        hermes_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "HERMES_CPU_RESERVATION" "0.5" "$hermes_cpu_limit")"
        comfyui_cpu_limit="$(select_env_service_cpu_limit "$env_path" "COMFYUI_CPU_LIMIT" "16.0" "$docker_available_cpus")"
        comfyui_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "COMFYUI_CPU_RESERVATION" "2.0" "$comfyui_cpu_limit")"
        upsert_env_value "$env_path" "TTS_CPU_LIMIT" "$tts_cpu_limit"
        upsert_env_value "$env_path" "TTS_CPU_RESERVATION" "$tts_cpu_reservation"
        upsert_env_value "$env_path" "WHISPER_CPU_LIMIT" "$whisper_cpu_limit"
        upsert_env_value "$env_path" "WHISPER_CPU_RESERVATION" "$whisper_cpu_reservation"
        upsert_env_value "$env_path" "HERMES_CPU_LIMIT" "$hermes_cpu_limit"
        upsert_env_value "$env_path" "HERMES_CPU_RESERVATION" "$hermes_cpu_reservation"
        upsert_env_value "$env_path" "COMFYUI_CPU_LIMIT" "$comfyui_cpu_limit"
        upsert_env_value "$env_path" "COMFYUI_CPU_RESERVATION" "$comfyui_cpu_reservation"

        # Upsert ODS_AGENT_KEY when missing (pre-PR-#979 upgrade path)
        if [[ -z "$(read_env_value "$env_path" "ODS_AGENT_KEY")" ]]; then
            upsert_env_value "$env_path" "ODS_AGENT_KEY" "$(new_secure_hex 32)"
        fi
        # Dashboard API runs in Docker; host-agent stays loopback-only on
        # Docker Desktop and is reachable from containers via this hostname.
        if [[ -z "$(read_env_value "$env_path" "ODS_AGENT_HOST")" ]]; then
            upsert_env_value "$env_path" "ODS_AGENT_HOST" "host.docker.internal"
        fi
        # Upsert SHIELD_API_KEY when missing (Privacy Shield cross-service auth)
        if [[ -z "$(read_env_value "$env_path" "SHIELD_API_KEY")" ]]; then
            upsert_env_value "$env_path" "SHIELD_API_KEY" "$(new_secure_hex 32)"
        fi
        # Upsert TOKEN_SPY_API_KEY when missing so dashboard-api and the
        # Token Spy UI share the same login key. Preserve Token Spy's existing
        # fallback key file on upgrades where the container created one first.
        if [[ -z "$(read_env_value "$env_path" "TOKEN_SPY_API_KEY")" ]]; then
            local _token_spy_api_key
            _token_spy_api_key="$(read_token_spy_api_key "$install_dir")"
            if [[ -z "$_token_spy_api_key" ]]; then
                _token_spy_api_key="$(new_secure_hex 32)"
            fi
            upsert_env_value "$env_path" "TOKEN_SPY_API_KEY" "$_token_spy_api_key"
        fi
        # Upsert ODS_SESSION_SECRET when missing — HMAC key for the
        # ods-session cookie minted by magic-link redemption. Without
        # this, dashboard-api refuses to issue cookies (and the magic-link
        # gate effectively breaks). Rotating invalidates every issued cookie.
        if [[ -z "$(read_env_value "$env_path" "ODS_SESSION_SECRET")" ]]; then
            upsert_env_value "$env_path" "ODS_SESSION_SECRET" "$(new_secure_hex 32)"
        fi
        # ODS_DEVICE_NAME backfill: older macOS installs omitted this key,
        # so magic links defaulted to auth.ods.local/chat.ods.local and
        # collided with every other default install on the LAN.
        if [[ -z "$(read_env_value "$env_path" "ODS_DEVICE_NAME")" ]]; then
            upsert_env_value "$env_path" "ODS_DEVICE_NAME" "$(detect_device_name)"
        fi

        # HOST_LAN_IP backfill: the fresh-install heredoc below populates
        # HOST_LAN_IP when BIND_ADDRESS=0.0.0.0 was pre-set, so openclaw can
        # extend allowedOrigins for LAN clients. Pre-existing installs that
        # opted into LAN mode (BIND_ADDRESS=0.0.0.0 in their .env) but were
        # generated before this code shipped have no HOST_LAN_IP — openclaw
        # then rejects LAN client requests until a manual .env edit. Detect
        # and upsert when missing, gated by the operator's existing BIND_ADDRESS
        # opt-in. Linux Phase 06 doesn't need this — it always reads HOST_LAN_IP
        # via _env_get unconditionally.
        local _existing_bind
        _existing_bind=$(read_env_value "$env_path" "BIND_ADDRESS")
        if [[ "$_existing_bind" == "0.0.0.0" ]] && [[ -z "$(read_env_value "$env_path" "HOST_LAN_IP")" ]]; then
            local _host_lan_ip
            _host_lan_ip=$(detect_host_lan_ip)
            if [[ -n "$_host_lan_ip" ]]; then
                upsert_env_value "$env_path" "HOST_LAN_IP" "$_host_lan_ip"
            fi
        fi
        return 0
    fi

    # Generate secrets
    local webui_secret
    webui_secret=$(new_secure_hex 32)
    local n8n_pass
    n8n_pass=$(new_secure_base64 16)
    local litellm_key
    litellm_key="sk-ods-$(new_secure_hex 16)"
    local livekit_secret
    livekit_secret=$(new_secure_base64 32)
    local livekit_api_key
    livekit_api_key=$(new_secure_hex 16)
    local dashboard_api_key
    dashboard_api_key=$(new_secure_hex 32)
    local ods_agent_key
    ods_agent_key=$(new_secure_hex 32)
    local ods_session_secret
    ods_session_secret=$(new_secure_hex 32)
    local shield_api_key
    shield_api_key=$(new_secure_hex 32)
    local token_spy_api_key
    token_spy_api_key="$(read_token_spy_api_key "$install_dir")"
    if [[ -z "$token_spy_api_key" ]]; then
        token_spy_api_key=$(new_secure_hex 32)
    fi
    tts_cpu_limit="$(select_env_service_cpu_limit "$env_path" "TTS_CPU_LIMIT" "8.0" "$docker_available_cpus")"
    tts_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "TTS_CPU_RESERVATION" "2.0" "$tts_cpu_limit")"
    whisper_cpu_limit="$(select_env_service_cpu_limit "$env_path" "WHISPER_CPU_LIMIT" "4.0" "$docker_available_cpus")"
    whisper_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "WHISPER_CPU_RESERVATION" "1.0" "$whisper_cpu_limit")"
    hermes_cpu_limit="$(select_env_service_cpu_limit "$env_path" "HERMES_CPU_LIMIT" "4.0" "$docker_available_cpus")"
    hermes_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "HERMES_CPU_RESERVATION" "0.5" "$hermes_cpu_limit")"
    comfyui_cpu_limit="$(select_env_service_cpu_limit "$env_path" "COMFYUI_CPU_LIMIT" "16.0" "$docker_available_cpus")"
    comfyui_cpu_reservation="$(select_env_service_cpu_reservation "$env_path" "COMFYUI_CPU_RESERVATION" "2.0" "$comfyui_cpu_limit")"
    local openclaw_token
    openclaw_token=$(new_secure_hex 24)
    local qdrant_api_key
    qdrant_api_key=$(new_secure_hex 32)
    local opencode_password
    opencode_password=$(new_secure_base64 16)
    local searxng_secret
    searxng_secret=$(new_secure_hex 32)
    # Langfuse (LLM Observability)
    # NOTE: macOS env-generator always regenerates secrets (no merge logic).
    # If reinstalling with existing Langfuse data, run: rm -rf data/langfuse/
    local langfuse_nextauth_secret
    langfuse_nextauth_secret=$(new_secure_hex 32)
    local langfuse_salt
    langfuse_salt=$(new_secure_hex 32)
    local langfuse_encryption_key
    langfuse_encryption_key=$(new_secure_hex 32)
    local langfuse_db_password
    langfuse_db_password=$(new_secure_hex 16)
    local langfuse_clickhouse_password
    langfuse_clickhouse_password=$(new_secure_hex 16)
    local langfuse_redis_password
    langfuse_redis_password=$(new_secure_hex 16)
    local langfuse_minio_access_key
    langfuse_minio_access_key=$(new_secure_hex 16)
    local langfuse_minio_secret_key
    langfuse_minio_secret_key=$(new_secure_hex 32)
    local langfuse_project_public_key
    langfuse_project_public_key="pk-lf-ods-$(new_secure_hex 16)"
    local langfuse_project_secret_key
    langfuse_project_secret_key="sk-lf-ods-$(new_secure_hex 16)"
    local langfuse_init_project_id
    langfuse_init_project_id=$(new_secure_hex 16)
    local langfuse_init_user_password
    langfuse_init_user_password=$(new_secure_hex 16)
    # Colima's user-mode host.docker.internal route can become unreachable
    # under load. The orchestrator enables its private vmnet address first;
    # bridge loopback-only host services through that scoped interface.
    local macos_llm_bridge_enabled="false"
    local macos_host_agent_bridge_enabled="false"
    local native_llama_port="8080"
    local macos_host_gateway=""
    local macos_vm_ip=""
    local agent_host="host.docker.internal"
    local llm_api_url="http://host.docker.internal:8080"
    if [[ "${DOCKER_BACKEND:-unknown}" == "colima" ]]; then
        macos_llm_bridge_enabled="true"
        macos_host_agent_bridge_enabled="true"
        native_llama_port="8080"
        macos_host_gateway="${COLIMA_HOST_IP:-}"
        macos_vm_ip="${COLIMA_VM_IP:-}"
        if [[ -n "$macos_host_gateway" ]]; then
            agent_host="$macos_host_gateway"
            llm_api_url="http://${macos_host_gateway}:8080"
        fi
    fi

    # Host LAN IP — only populated when the operator has pre-set
    # BIND_ADDRESS=0.0.0.0 in the environment (macOS has no --lan flag).
    # Used by openclaw to extend allowedOrigins for LAN clients.
    local host_lan_ip=""
    if [[ "${BIND_ADDRESS:-127.0.0.1}" == "0.0.0.0" ]]; then
        host_lan_ip=$(detect_host_lan_ip)
    fi
    local device_name
    device_name=$(detect_device_name)

    local tz
    tz=$(detect_timezone)
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Build .env content (matches Phase 06 format)
    cat > "$env_path" << ENVEOF
# ODS Configuration -- ${TIER_NAME} Edition
# Generated by macOS installer v${ODS_VERSION} on ${timestamp}
# Tier: ${tier} (${TIER_NAME})

#=== Network Binding ===
# macOS has no --lan flag; operators opt in by setting BIND_ADDRESS=0.0.0.0
# manually. HOST_LAN_IP is only populated when that pre-existed at install time.
HOST_LAN_IP=${host_lan_ip}
# Device name used by ods-mdns/ods-proxy hostnames and magic-link URLs.
# Derived from the macOS LocalHostName/hostname so multiple installs on one LAN
# do not all collide on auth.ods.local/chat.ods.local.
ODS_DEVICE_NAME=${device_name}
# Container route to the loopback-only host agent (private Colima bridge or Docker Desktop helper).
ODS_AGENT_HOST=${ODS_AGENT_HOST:-${agent_host}}

#=== LLM Backend Mode ===
ODS_MODE=local
LLM_API_URL=${llm_api_url}
LLM_BACKEND=llama-server

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
TOGETHER_API_KEY=
MINIMAX_API_KEY=

#=== LLM Settings (llama-server -- native Metal) ===
ODS_MACOS_LLM_BRIDGE_ENABLED=${macos_llm_bridge_enabled}
ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED=${macos_host_agent_bridge_enabled}
ODS_MACOS_HOST_GATEWAY=${macos_host_gateway}
ODS_MACOS_VM_IP=${macos_vm_ip}
ODS_NATIVE_LLAMA_PORT=${native_llama_port}
MODEL_PROFILE=${MODEL_PROFILE_REQUESTED:-${MODEL_PROFILE:-qwen}}
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
MODEL_RECOMMENDATION_REASON=${MODEL_RECOMMENDATION_REASON:-Selected by installer tier ${tier} (${TIER_NAME}) for apple backend; benchmark locally after first launch.}
MODEL_RECOMMENDED_ALTERNATIVES=${MODEL_RECOMMENDED_ALTERNATIVES:-}
MODEL_PERFORMANCE_SOURCE=benchmark_required
MODEL_PERFORMANCE_LABEL=Benchmark after first launch
GPU_BACKEND=apple
HOST_RAM_GB=${SYSTEM_RAM_GB}
$(if [[ -n "${LLAMA_SERVER_IMAGE:-}" ]]; then echo "LLAMA_SERVER_IMAGE=${LLAMA_SERVER_IMAGE}"; fi)
#=== llama.cpp Runtime Tuning ===
LLAMA_ARG_FLASH_ATTN=${LLAMA_ARG_FLASH_ATTN:-auto}
LLAMA_ARG_CACHE_TYPE_K=${LLAMA_ARG_CACHE_TYPE_K:-f16}
LLAMA_ARG_CACHE_TYPE_V=${LLAMA_ARG_CACHE_TYPE_V:-f16}
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
# Optional MTP speculative decoding only. Requires an MTP-capable GGUF and llama.cpp build.
# LLAMA_ARG_SPEC_TYPE=draft-mtp
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3
LLAMA_CPU_LIMIT=${detected_cpu_limit}
LLAMA_CPU_RESERVATION=${detected_cpu_reservation}

#=== Bundled Service CPU Budgets ===
TTS_CPU_LIMIT=${tts_cpu_limit}
TTS_CPU_RESERVATION=${tts_cpu_reservation}
WHISPER_CPU_LIMIT=${whisper_cpu_limit}
WHISPER_CPU_RESERVATION=${whisper_cpu_reservation}
HERMES_CPU_LIMIT=${hermes_cpu_limit}
HERMES_CPU_RESERVATION=${hermes_cpu_reservation}
COMFYUI_CPU_LIMIT=${comfyui_cpu_limit}
COMFYUI_CPU_RESERVATION=${comfyui_cpu_reservation}

#=== Ports ===
OLLAMA_PORT=8080
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
LANGFUSE_PORT=3006

#=== Hermes Agent ===
# macOS runs llama-server natively with Metal; containers use the scoped host route above.
HERMES_LLM_BASE_URL=${llm_api_url}/v1
HERMES_LLM_API_KEY=sk-ods-hermes-local
HERMES_LANGUAGE=en
HERMES_PROXY_PORT=9120
HERMES_PROXY_UPSTREAM=ods-hermes:9119
ODS_AUTH_UPSTREAM=ods-dashboard-api:3002

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=${webui_secret}
DASHBOARD_API_KEY=${dashboard_api_key}
ODS_AGENT_KEY=${ods_agent_key}
ODS_SESSION_SECRET=${ods_session_secret}
SHIELD_API_KEY=${shield_api_key}
N8N_USER=admin@ods.local
N8N_PASS=${n8n_pass}
LITELLM_KEY=${litellm_key}
LIVEKIT_API_KEY=${livekit_api_key}
LIVEKIT_API_SECRET=${livekit_secret}
OPENCLAW_TOKEN=${openclaw_token}
QDRANT_API_KEY=${qdrant_api_key}
TOKEN_SPY_API_KEY=${token_spy_api_key}
SEARXNG_SECRET=${searxng_secret}

#=== OpenCode Settings ===
OPENCODE_PORT=3003
OPENCODE_SERVER_PASSWORD=${opencode_password}

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper STT model. macOS (Apple Silicon, Metal) uses base by default —
# Metal performance is good enough for most transcription needs. Override
# here and run 'ods-macos.sh restart' to use a different model.
AUDIO_STT_MODEL=Systran/faster-whisper-base
TTS_VOICE=en_US-lessac-medium

#=== Web UI Settings ===
WEBUI_AUTH=true
ENABLE_WEB_SEARCH=true
WEB_SEARCH_ENGINE=searxng

#=== n8n Settings ===
N8N_HOST=localhost
N8N_WEBHOOK_URL=http://localhost:5678
TIMEZONE=${tz}

#=== Langfuse (LLM Observability) ===
# NOTE: this value is only written on first install or --force (the macOS
# env-generator early-returns when .env already exists). Users who re-run
# ./install-macos.sh --langfuse on an existing install should instead use
# post-install: 'ods enable langfuse'.
LANGFUSE_ENABLED=${ENABLE_LANGFUSE:-false}
LANGFUSE_NEXTAUTH_SECRET=${langfuse_nextauth_secret}
LANGFUSE_SALT=${langfuse_salt}
LANGFUSE_ENCRYPTION_KEY=${langfuse_encryption_key}
LANGFUSE_DB_PASSWORD=${langfuse_db_password}
LANGFUSE_CLICKHOUSE_PASSWORD=${langfuse_clickhouse_password}
LANGFUSE_REDIS_PASSWORD=${langfuse_redis_password}
LANGFUSE_MINIO_ACCESS_KEY=${langfuse_minio_access_key}
LANGFUSE_MINIO_SECRET_KEY=${langfuse_minio_secret_key}
LANGFUSE_PROJECT_PUBLIC_KEY=${langfuse_project_public_key}
LANGFUSE_PROJECT_SECRET_KEY=${langfuse_project_secret_key}
LANGFUSE_INIT_PROJECT_ID=${langfuse_init_project_id}
LANGFUSE_INIT_USER_EMAIL=admin@ods.local
LANGFUSE_INIT_USER_PASSWORD=${langfuse_init_user_password}
ENVEOF

    # Restrict .env to current user only (chmod 600)
    chmod 600 "$env_path" 2>/dev/null || true

    # Export secrets for use by other generators
    ENV_SEARXNG_SECRET="$searxng_secret"
    ENV_OPENCLAW_TOKEN="$openclaw_token"
    ENV_DASHBOARD_KEY="$dashboard_api_key"
}

generate_searxng_config() {
    local install_dir="$1"
    local secret_key="$2"
    local force_overwrite="${3:-false}"

    local config_dir="${install_dir}/config/searxng"
    mkdir -p "$config_dir"
    local settings_path="${config_dir}/settings.yml"

    # Idempotency: preserve existing SearXNG config unless forced.
    if [[ -f "$settings_path" ]] && [[ "$force_overwrite" != "true" ]]; then
        return 0
    fi

    cat > "$settings_path" << SEARXEOF
use_default_settings: true
server:
  secret_key: "${secret_key}"
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
SEARXEOF
}

generate_openclaw_config() {
    local install_dir="$1"
    local llm_model="$2"
    local max_context="$3"
    local token="$4"
    local provider_url="${5:-http://host.docker.internal:8080}"
    local force_overwrite="${6:-false}"
    local provider_api_key="${7:-none}"
    local provider_name="local-llama"

    # Create directories
    local home_dir="${install_dir}/data/openclaw/home"
    local agent_dir="${home_dir}/agents/main/agent"
    local canvas_dir="${home_dir}/canvas"
    local cron_dir="${home_dir}/cron"
    local sess_dir="${home_dir}/agents/main/sessions"
    mkdir -p "$agent_dir" "$canvas_dir" "$cron_dir" "$sess_dir"

    # Preserve unrelated user configuration, but always refresh ODS's managed
    # provider on local/cloud transitions so an old endpoint or key cannot win.
    if [[ -f "${home_dir}/openclaw.json" ]] && [[ "$force_overwrite" != "true" ]]; then
        ODS_OPENCLAW_HOME_CONFIG="${home_dir}/openclaw.json" \
        ODS_OPENCLAW_AUTH_CONFIG="${agent_dir}/auth-profiles.json" \
        ODS_OPENCLAW_MODELS_CONFIG="${agent_dir}/models.json" \
        ODS_OPENCLAW_PROVIDER="$provider_name" \
        ODS_OPENCLAW_MODEL="$llm_model" \
        ODS_OPENCLAW_CONTEXT="$max_context" \
        ODS_OPENCLAW_BASE_URL="$provider_url" \
        ODS_OPENCLAW_API_KEY="$provider_api_key" \
            python3 - <<'OPENCLAW_REFRESH_PY'
import json
import os
from pathlib import Path

provider_id = os.environ["ODS_OPENCLAW_PROVIDER"]
model_id = os.environ["ODS_OPENCLAW_MODEL"]
context = int(os.environ["ODS_OPENCLAW_CONTEXT"])
base_url = os.environ["ODS_OPENCLAW_BASE_URL"]
api_key = os.environ["ODS_OPENCLAW_API_KEY"]
provider_model = f"{provider_id}/{model_id}"

model_entry = {
    "id": model_id,
    "name": "ODS LLM",
    "reasoning": False,
    "input": ["text"],
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    "contextWindow": context,
    "maxTokens": min(8192, context),
    "compat": {
        "supportsStore": False,
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": False,
        "maxTokensField": "max_tokens",
    },
}

def load(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

home_path = Path(os.environ["ODS_OPENCLAW_HOME_CONFIG"])
home = load(home_path)
provider = home.setdefault("models", {}).setdefault("providers", {}).setdefault(provider_id, {})
provider.update({
    "baseUrl": base_url,
    "apiKey": api_key,
    "api": "openai-completions",
    "models": [model_entry],
})
defaults = home.setdefault("agents", {}).setdefault("defaults", {})
defaults["model"] = {"primary": provider_model}
defaults["models"] = {provider_model: {}}
defaults.setdefault("subagents", {})["model"] = provider_model
save(home_path, home)

auth_path = Path(os.environ["ODS_OPENCLAW_AUTH_CONFIG"])
auth = load(auth_path)
auth.setdefault("version", 1)
auth.setdefault("profiles", {})[f"{provider_id}:default"] = {
    "type": "api_key",
    "provider": provider_id,
    "key": api_key,
}
auth.setdefault("lastGood", {})[provider_id] = f"{provider_id}:default"
auth.setdefault("usageStats", {})
save(auth_path, auth)

models_path = Path(os.environ["ODS_OPENCLAW_MODELS_CONFIG"])
models = load(models_path)
models.setdefault("providers", {})[provider_id] = {
    "baseUrl": base_url,
    "apiKey": api_key,
    "api": "openai-completions",
    "models": [model_entry],
}
save(models_path, models)

for path in (home_path, auth_path, models_path):
    check = load(path)
    if not check:
        raise SystemExit(f"OpenClaw config verification failed: {path}")
OPENCLAW_REFRESH_PY
        return 0
    fi

    # Home config
    cat > "${home_dir}/openclaw.json" << OCEOF
{
  "models": {
    "providers": {
      "${provider_name}": {
        "baseUrl": "${provider_url}",
        "apiKey": "${provider_api_key}",
        "api": "openai-completions",
        "models": [
          {
            "id": "${llm_model}",
            "name": "ODS LLM",
            "reasoning": false,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": ${max_context},
            "maxTokens": 8192,
            "compat": {
              "supportsStore": false,
              "supportsDeveloperRole": false,
              "supportsReasoningEffort": false,
              "maxTokensField": "max_tokens"
            }
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {"primary": "${provider_name}/${llm_model}"},
      "models": {"${provider_name}/${llm_model}": {}},
      "compaction": {"mode": "safeguard"},
      "subagents": {"maxConcurrent": 20, "model": "${provider_name}/${llm_model}"}
    }
  },
  "commands": {"native": "auto", "nativeSkills": "auto"},
  "gateway": {
    "mode": "local",
    "bind": "lan",
    "controlUi": {"allowInsecureAuth": true},
    "auth": {"mode": "token", "token": "${token}"}
  }
}
OCEOF

    # Auth profiles
    cat > "${agent_dir}/auth-profiles.json" << AUTHEOF
{
  "version": 1,
  "profiles": {
    "${provider_name}:default": {
      "type": "api_key",
      "provider": "${provider_name}",
      "key": "${provider_api_key}"
    }
  },
  "lastGood": {"${provider_name}": "${provider_name}:default"},
  "usageStats": {}
}
AUTHEOF

    # Models config
    cat > "${agent_dir}/models.json" << MODEOF
{
  "providers": {
    "${provider_name}": {
      "baseUrl": "${provider_url}",
      "apiKey": "${provider_api_key}",
      "api": "openai-completions",
      "models": [
        {
          "id": "${llm_model}",
          "name": "ODS LLM",
          "reasoning": false,
          "input": ["text"],
          "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
          "contextWindow": ${max_context},
          "maxTokens": 8192,
          "compat": {
            "supportsStore": false,
            "supportsDeveloperRole": false,
            "supportsReasoningEffort": false,
            "maxTokensField": "max_tokens"
          }
        }
      ]
    }
  }
}
MODEOF

    chmod 600 "${home_dir}/openclaw.json" \
        "${agent_dir}/auth-profiles.json" "${agent_dir}/models.json"

    # Workspace directory
    mkdir -p "${install_dir}/config/openclaw/workspace/memory"
}

# Auto-configure Perplexica to use local llama-server
configure_perplexica() {
    local perplexica_port="${1:-3004}"
    local llm_model="${2:-default}"
    local llm_base_url="${3:-${LLM_API_URL:-http://host.docker.internal:8080}}"
    local api_key="${4:-no-key}"
    local perplexica_url="http://localhost:${perplexica_port}"

    case "$llm_base_url" in
        */v1|*/api/v1) ;;
        *) llm_base_url="${llm_base_url%/}/v1" ;;
    esac

    PYTHON_CMD="python3"
    if [[ -f "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/python-cmd.sh" ]]; then
        . "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/python-cmd.sh"
        PYTHON_CMD="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    # Current Perplexica images start with only the Transformers provider. Use
    # the provider API to create or update the managed OpenAI-compatible route,
    # then verify the exact persisted endpoint, key, model, and preferences.
    PERPLEXICA_URL="$perplexica_url" \
    PERPLEXICA_MODEL="$llm_model" \
    PERPLEXICA_LLM_BASE_URL="$llm_base_url" \
    PERPLEXICA_API_KEY="$api_key" \
        "$PYTHON_CMD" - <<'PERPLEXICA_CONFIG_PY' >/dev/null 2>&1
import json
import os
import urllib.request

root = os.environ["PERPLEXICA_URL"].rstrip("/")
model = os.environ["PERPLEXICA_MODEL"]
base_url = os.environ["PERPLEXICA_LLM_BASE_URL"]
api_key = os.environ["PERPLEXICA_API_KEY"] or "no-key"


def request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        root + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


values = request("GET", "/api/config").get("values", {})
providers = values.get("modelProviders") or []
provider = next((item for item in providers if item.get("type") == "openai"), None)
if provider is None:
    provider = request(
        "POST",
        "/api/providers",
        {
            "type": "openai",
            "name": "ODS inference",
            "config": {"apiKey": api_key, "baseURL": base_url},
        },
    ).get("provider")
else:
    config = dict(provider.get("config") or {})
    config.update({"apiKey": api_key, "baseURL": base_url})
    provider = request(
        "PATCH",
        f"/api/providers/{provider['id']}",
        {"name": provider.get("name") or "ODS inference", "config": config},
    ).get("provider")
if not isinstance(provider, dict) or not provider.get("id"):
    raise SystemExit("Perplexica provider configuration failed")

provider_id = provider["id"]
models = provider.get("chatModels") or []
if not any(item.get("key") == model or item.get("name") == model for item in models):
    request(
        "POST",
        f"/api/providers/{provider_id}/models",
        {"key": model, "name": model, "type": "chat"},
    )

transformers = next((item for item in providers if item.get("type") == "transformers"), None)
request(
    "POST",
    "/api/config",
    {
        "key": "preferences",
        "value": {
            "defaultChatProvider": provider_id,
            "defaultChatModel": model,
            "defaultEmbeddingProvider": transformers["id"] if transformers else provider_id,
            "defaultEmbeddingModel": "Xenova/all-MiniLM-L6-v2",
        },
    },
)
try:
    request("POST", "/api/config/setup-complete", {})
except Exception:
    request("POST", "/api/config", {"key": "setupComplete", "value": True})

check = request("GET", "/api/config").get("values", {})
saved = next(
    (item for item in check.get("modelProviders", []) if item.get("id") == provider_id),
    {},
)
saved_config = saved.get("config") or {}
saved_models = saved.get("chatModels") or []
preferences = check.get("preferences") or {}
ok = (
    check.get("setupComplete")
    and saved_config.get("baseURL") == base_url
    and saved_config.get("apiKey") == api_key
    and any(item.get("key") == model or item.get("name") == model for item in saved_models)
    and preferences.get("defaultChatProvider") == provider_id
    and preferences.get("defaultChatModel") == model
)
raise SystemExit(0 if ok else 1)
PERPLEXICA_CONFIG_PY
}
