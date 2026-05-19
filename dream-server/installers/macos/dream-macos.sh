#!/bin/bash
# ============================================================================
# Dream Server macOS CLI -- dream-macos.sh
# ============================================================================
# Day-to-day management of a Dream Server installation on macOS.
# Mirrors the Windows dream.ps1 command structure.
#
# Usage:
#   ./dream-macos.sh status              # Health checks + Apple Silicon info
#   ./dream-macos.sh start [service]     # Start all or one service
#   ./dream-macos.sh stop [service]      # Stop all or one service
#   ./dream-macos.sh restart [service]   # Restart all or one service
#   ./dream-macos.sh logs <service> [N]  # Tail logs (default 100 lines)
#   ./dream-macos.sh config show         # View .env (secrets masked)
#   ./dream-macos.sh config edit         # Open .env in $EDITOR
#   ./dream-macos.sh chat "message"      # Quick chat via API
#   ./dream-macos.sh update              # Pull latest images and restart
#   ./dream-macos.sh version             # Show version
#   ./dream-macos.sh help                # Show help
#
# ============================================================================

# Guard: macOS ships Bash 3.2 (GPL). dream-cli and our libs need Bash 4+.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  # Default candidate paths cover standard Apple Silicon and Intel Homebrew
  # prefixes. If brew is already on PATH we also ask it for its actual prefix,
  # which handles custom installs (e.g. /Volumes/X/homebrew).
  candidates=(/opt/homebrew/bin/bash /usr/local/bin/bash)
  if command -v brew >/dev/null 2>&1; then
    brew_prefix="$(brew --prefix 2>/dev/null)"
    [ -n "$brew_prefix" ] && candidates=("$brew_prefix/bin/bash" "${candidates[@]}")
  fi
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ]; then
      exec "$candidate" "$0" "$@"
    fi
  done
  if ! command -v brew >/dev/null 2>&1; then
    echo "DreamServer requires Bash 4+ (you have ${BASH_VERSION})." >&2
    echo "Install Homebrew first:" >&2
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"" >&2
    echo "Then re-run this installer." >&2
    exit 1
  fi
  echo "Installing Bash 4+ via Homebrew (one-time setup)..."
  brew install bash || { echo "brew install bash failed" >&2; exit 1; }
  brew_prefix="$(brew --prefix 2>/dev/null)"
  if [ -n "$brew_prefix" ] && [ -x "$brew_prefix/bin/bash" ]; then
    exec "$brew_prefix/bin/bash" "$0" "$@"
  fi
  for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [ -x "$candidate" ]; then
      exec "$candidate" "$0" "$@"
    fi
  done
  echo "Homebrew bash installed but not found in expected paths." >&2
  exit 1
fi

set -euo pipefail

# ── Locate libraries ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# Hint resolve_install_dir() that the script lives inside a populated install.
# Lets `bash /path/to/install/dream-macos.sh status` work without DREAM_HOME
# when /path/to/install contains an installer-generated .env sentinel. Unset
# after the sourced chain so the hint does not leak into child processes we
# spawn later (docker compose, curl, etc.).
export DREAM_SCRIPT_HINT="$SCRIPT_DIR"

# Source only what we need for CLI
source "${LIB_DIR}/constants.sh"
source "${LIB_DIR}/ui.sh"
source "${LIB_DIR}/detection.sh"

unset DREAM_SCRIPT_HINT

# ── Resolve install directory ──
INSTALL_DIR="${DS_INSTALL_DIR}"

# ============================================================================
# Helpers
# ============================================================================

test_docker_running() {
    if ! docker info >/dev/null 2>&1; then
        ai_err "Docker Desktop is not running."
        ai "Start it from the Applications folder or menu bar, then try again."
        return 1
    fi
    return 0
}

test_install() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        ai_err "Dream Server not found at ${INSTALL_DIR}."
        ai "Invoke from inside the install dir (bash <install>/dream-macos.sh status), export DREAM_HOME=<install>, or run the installer."
        exit 1
    fi
    local base_compose="${INSTALL_DIR}/docker-compose.base.yml"
    local mono_compose="${INSTALL_DIR}/docker-compose.yml"
    if [[ ! -f "$base_compose" ]] && [[ ! -f "$mono_compose" ]]; then
        ai_err "docker-compose.base.yml not found in ${INSTALL_DIR}"
        exit 1
    fi
    test_docker_running || exit 1
}

get_compose_flags() {
    local flags_file="${INSTALL_DIR}/.compose-flags"
    if [[ -f "$flags_file" ]]; then
        cat "$flags_file"
        return
    fi
    # Fallback: dynamic resolution via resolve-compose-stack.sh so user-installed
    # extensions in data/user-extensions/ are discovered when the .compose-flags
    # cache is missing or stale. Mirrors dream-cli's get_compose_flags fallback.
    if [[ -x "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" ]]; then
        # Pass --gpu-count for parity with the Linux paths even though there's
        # currently no docker-compose.multigpu-apple.yml — keeps the contract
        # uniform across all resolver call sites.
        "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "${TIER:-1}" \
            --gpu-backend "${GPU_BACKEND:-apple}" \
            --gpu-count "${GPU_COUNT:-1}"
        return
    fi
    # Last resort: resolver script missing — emit base + macos overlay
    local flags="-f docker-compose.base.yml"
    if [[ -f "${INSTALL_DIR}/installers/macos/docker-compose.macos.yml" ]]; then
        flags="$flags -f installers/macos/docker-compose.macos.yml"
    fi
    echo "$flags"
}

read_dream_env() {
    local env_file="${INSTALL_DIR}/.env"
    if [[ ! -f "$env_file" ]]; then
        return
    fi
    # Parse .env safely (no eval)
    while IFS= read -r line; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ "$line" =~ ^# ]] && continue
        [[ -z "$line" ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"
            val=$(echo "$val" | sed 's/^["'"'"']//;s/["'"'"']$//')
            export "ENV_${key}=${val}"
        fi
    done < "$env_file"
}

read_env_value() {
    local env_file="$1"
    local key="$2"
    [[ -f "$env_file" ]] || { echo ""; return 0; }
    grep -E "^${key}=" "$env_file" 2>/dev/null | sed -n '1p' | cut -d'=' -f2- | tr -d '\r' || true
}

upsert_env_value() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    if grep -qE "^${key}=" "$env_file" 2>/dev/null; then
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

select_auto_cpu_value() {
    local existing="$1"
    local detected="$2"
    if [[ "$existing" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk "BEGIN { exit !($existing > 0 && $existing <= $detected) }"; then
        echo "$existing"
    else
        echo "$detected"
    fi
}

ensure_llama_cpu_budget() {
    local env_file="${INSTALL_DIR}/.env"
    [[ -f "$env_file" ]] || return 0

    local backend
    backend="$(read_env_value "$env_file" "GPU_BACKEND")"
    backend=$(echo "${backend:-apple}" | tr '[:upper:]' '[:lower:]')
    [[ "$backend" == "none" ]] && backend="cpu"

    local limit_raw reservation_raw available
    read -r limit_raw reservation_raw available <<< "$(calculate_llama_cpu_budget "$backend")"

    local detected_limit="${limit_raw}.0"
    local detected_reservation="${reservation_raw}.0"
    local current_limit current_reservation final_limit final_reservation
    current_limit="$(read_env_value "$env_file" "LLAMA_CPU_LIMIT")"
    current_reservation="$(read_env_value "$env_file" "LLAMA_CPU_RESERVATION")"
    final_limit="$(select_auto_cpu_value "$current_limit" "$detected_limit")"
    final_reservation="$(select_auto_cpu_value "$current_reservation" "$detected_reservation")"

    if awk "BEGIN { exit !($final_reservation > $final_limit) }"; then
        final_reservation="$final_limit"
    fi

    local changed=false
    if [[ "$current_limit" != "$final_limit" ]]; then
        upsert_env_value "$env_file" "LLAMA_CPU_LIMIT" "$final_limit"
        changed=true
    fi
    if [[ "$current_reservation" != "$final_reservation" ]]; then
        upsert_env_value "$env_file" "LLAMA_CPU_RESERVATION" "$final_reservation"
        changed=true
    fi

    if [[ "$changed" == "true" ]]; then
        ai "Auto-adjusted llama-server CPU budget: limit=${final_limit}, reservation=${final_reservation} (Docker CPUs: ${available})"
    fi
}

# ── Native llama-server management ──

get_native_llama_status() {
    NATIVE_LLAMA_RUNNING=false
    NATIVE_LLAMA_PID=0
    NATIVE_LLAMA_HEALTHY=false

    if [[ ! -f "$LLAMA_SERVER_PID_FILE" ]]; then
        return
    fi

    local saved_pid
    saved_pid=$(cat "$LLAMA_SERVER_PID_FILE" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$saved_pid" ]] && return

    if kill -0 "$saved_pid" 2>/dev/null; then
        NATIVE_LLAMA_RUNNING=true
        NATIVE_LLAMA_PID="$saved_pid"

        # Health check
        if curl -sf --max-time 10 http://127.0.0.1:8080/health >/dev/null 2>&1; then
            NATIVE_LLAMA_HEALTHY=true
        fi
    else
        # Clean up stale PID file
        rm -f "$LLAMA_SERVER_PID_FILE" 2>/dev/null
    fi
}

start_native_llama() {
    get_native_llama_status
    if $NATIVE_LLAMA_RUNNING; then
        ai_ok "Native llama-server already running (PID ${NATIVE_LLAMA_PID})"
        return
    fi

    if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
        ai_err "llama-server not found at ${LLAMA_SERVER_BIN}"
        ai "Re-run the installer to download it."
        return
    fi

    read_dream_env
    local gguf_file="${ENV_GGUF_FILE:-Qwen3.5-9B-Q4_K_M.gguf}"
    local ctx_size="${ENV_CTX_SIZE:-131072}"
    local model_path="${INSTALL_DIR}/data/models/${gguf_file}"

    if [[ ! -f "$model_path" ]]; then
        ai_err "Model not found: ${model_path}"
        return
    fi

    mkdir -p "$(dirname "$LLAMA_SERVER_PID_FILE")"

    local reasoning="${ENV_LLAMA_REASONING:-off}"
    # Map .env values (off/on/auto) to llama-server --reasoning-format values
    local reasoning_fmt
    case "$reasoning" in
        off)  reasoning_fmt="none" ;;
        on)   reasoning_fmt="deepseek" ;;
        *)    reasoning_fmt="$reasoning" ;;
    esac

    local -a llama_args=(
        --host "${ENV_BIND_ADDRESS:-127.0.0.1}" --port 8080
        --model "$model_path"
        --ctx-size "$ctx_size"
        --n-gpu-layers 999
        --reasoning-format "$reasoning_fmt"
        --metrics
    )
    [[ -n "${ENV_LLAMA_ARG_FLASH_ATTN:-}" ]] && llama_args+=(--flash-attn "$ENV_LLAMA_ARG_FLASH_ATTN")
    [[ -n "${ENV_LLAMA_ARG_CACHE_TYPE_K:-}" ]] && llama_args+=(--cache-type-k "$ENV_LLAMA_ARG_CACHE_TYPE_K")
    [[ -n "${ENV_LLAMA_ARG_CACHE_TYPE_V:-}" ]] && llama_args+=(--cache-type-v "$ENV_LLAMA_ARG_CACHE_TYPE_V")
    [[ -n "${ENV_LLAMA_ARG_N_CPU_MOE:-}" ]] && llama_args+=(--n-cpu-moe "$ENV_LLAMA_ARG_N_CPU_MOE")
    [[ -n "${ENV_LLAMA_ARG_SPEC_TYPE:-}" ]] && llama_args+=(--spec-type "$ENV_LLAMA_ARG_SPEC_TYPE")
    [[ -n "${ENV_LLAMA_ARG_SPEC_DRAFT_N_MAX:-}" ]] && llama_args+=(--spec-draft-n-max "$ENV_LLAMA_ARG_SPEC_DRAFT_N_MAX")

    "$LLAMA_SERVER_BIN" "${llama_args[@]}" > "$LLAMA_SERVER_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$LLAMA_SERVER_PID_FILE"

    ai_ok "Native llama-server started (PID ${pid})"
    ai "Waiting for health..."

    local max_wait=60
    local waited=0
    while [[ "$waited" -lt "$max_wait" ]]; do
        sleep 2
        waited=$((waited + 2))
        if curl -sf --max-time 10 http://127.0.0.1:8080/health >/dev/null 2>&1; then
            ai_ok "Native llama-server healthy"
            return
        fi
    done
    ai_warn "llama-server may still be loading model..."
}

stop_native_llama() {
    get_native_llama_status
    if ! $NATIVE_LLAMA_RUNNING; then
        ai "Native llama-server not running"
        return
    fi

    kill "$NATIVE_LLAMA_PID" 2>/dev/null || true
    sleep 2
    # Force kill if still running
    if kill -0 "$NATIVE_LLAMA_PID" 2>/dev/null; then
        kill -9 "$NATIVE_LLAMA_PID" 2>/dev/null || true
    fi
    rm -f "$LLAMA_SERVER_PID_FILE" 2>/dev/null
    ai_ok "Native llama-server stopped (PID ${NATIVE_LLAMA_PID})"
}

# ============================================================================
# Commands
# ============================================================================

cmd_status() {
    test_install
    cd "$INSTALL_DIR"

    local flags
    flags=$(get_compose_flags)

    echo ""
    echo -e "  ${GRN}Dream Server Status (macOS)${NC}"
    echo -e "  ${DGRN}$(printf -- '-%.0s' {1..40})${NC}"

    # Apple Silicon info
    get_apple_silicon_info
    get_system_ram_gb
    echo -e "  ${DGRN}Chip:${NC} ${WHT}${APPLE_CHIP}${NC}"
    echo -e "  ${DGRN}RAM:${NC}  ${WHT}${SYSTEM_RAM_GB} GB (unified memory)${NC}"

    # Native llama-server status
    get_native_llama_status
    if $NATIVE_LLAMA_RUNNING; then
        local health_str="loading"
        $NATIVE_LLAMA_HEALTHY && health_str="healthy"
        ai_ok "llama-server (native Metal): running PID ${NATIVE_LLAMA_PID} (${health_str})"
    else
        ai_warn "llama-server (native Metal): not running"
    fi

    # Docker services
    echo ""
    # shellcheck disable=SC2086
    docker compose $flags ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true

    # Health checks
    echo ""
    echo -e "  ${GRN}Health Checks${NC}"
    echo -e "  ${DGRN}$(printf -- '-%.0s' {1..40})${NC}"

    # Parallel arrays (Bash 3.2 compatible)
    local ep_names=("LLM API" "Chat UI" "Dashboard" "OpenCode (IDE)")
    local ep_urls=("http://127.0.0.1:8080/health" "http://127.0.0.1:3000" "http://127.0.0.1:3001" "http://127.0.0.1:3003")

    for ((i=0; i<${#ep_names[@]}; i++)); do
        local name="${ep_names[$i]}"
        local url="${ep_urls[$i]}"
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
        if [[ "$code" -ge 200 ]] && [[ "$code" -lt 400 ]]; then
            ai_ok "${name}: healthy"
        elif [[ "$code" == "401" ]] || [[ "$code" == "403" ]]; then
            ai_ok "${name}: healthy (auth-protected)"
        else
            ai_warn "${name}: not responding"
        fi
    done

    echo ""
}

cmd_start() {
    local service="${1:-}"
    test_install
    cd "$INSTALL_DIR"
    ensure_llama_cpu_budget

    # Start native llama-server first
    if [[ -z "$service" ]] && [[ -x "$LLAMA_SERVER_BIN" ]]; then
        start_native_llama
    fi

    local flags
    flags=$(get_compose_flags)

    if [[ -n "$service" ]]; then
        ai "Starting ${service}..."
        # shellcheck disable=SC2086
        docker compose $flags up -d "$service"
        ai_ok "${service} started"
    else
        ai "Starting all services..."
        # shellcheck disable=SC2086
        docker compose $flags up -d
        ai_ok "All services started"
    fi
}

cmd_stop() {
    local service="${1:-}"
    test_install
    cd "$INSTALL_DIR"

    local flags
    flags=$(get_compose_flags)

    if [[ -n "$service" ]]; then
        ai "Stopping ${service}..."
        # shellcheck disable=SC2086
        docker compose $flags stop "$service"
        ai_ok "${service} stopped"
    else
        ai "Stopping all services..."
        # shellcheck disable=SC2086
        docker compose $flags down

        # Stop native llama-server
        if [[ -f "$LLAMA_SERVER_PID_FILE" ]]; then
            stop_native_llama
        fi

        ai_ok "All services stopped"
    fi
}

cmd_restart() {
    local service="${1:-}"
    test_install
    cd "$INSTALL_DIR"
    ensure_llama_cpu_budget

    local flags
    flags=$(get_compose_flags)

    if [[ -n "$service" ]]; then
        ai "Restarting ${service}..."
        # shellcheck disable=SC2086
        docker compose $flags up -d "$service"
        ai_ok "${service} restarted"
    else
        # Restart native llama-server
        if [[ -f "$LLAMA_SERVER_PID_FILE" ]] || [[ -x "$LLAMA_SERVER_BIN" ]]; then
            stop_native_llama
            start_native_llama
        fi

        ai "Restarting all services..."
        # shellcheck disable=SC2086
        docker compose $flags up -d
        ai_ok "All services restarted"
    fi
}

cmd_logs() {
    local service="${1:-}"
    local lines="${2:-100}"

    if [[ -z "$service" ]]; then
        ai "Usage: ./dream-macos.sh logs <service> [lines]"
        ai "Services: llama-server, open-webui, dashboard-api, n8n, whisper, tts, ..."
        echo ""
        ai "For native llama-server logs:"
        ai "  tail -f ${LLAMA_SERVER_LOG}"
        return
    fi

    # Special case: llama-server logs from native process
    if [[ "$service" == "llama-server" ]] || [[ "$service" == "llama" ]]; then
        if [[ -f "$LLAMA_SERVER_LOG" ]]; then
            ai "Native llama-server logs (last ${lines} lines):"
            tail -n "$lines" "$LLAMA_SERVER_LOG"
        else
            ai_warn "No llama-server log file found at ${LLAMA_SERVER_LOG}"
        fi
        return
    fi

    test_install
    cd "$INSTALL_DIR"

    local flags
    flags=$(get_compose_flags)
    # shellcheck disable=SC2086
    docker compose $flags logs -f --tail "$lines" "$service"
}

cmd_config_show() {
    test_install

    echo ""
    echo -e "  ${GRN}Configuration${NC}"
    echo -e "  ${DGRN}Install dir: ${INSTALL_DIR}${NC}"
    echo ""

    local env_file="${INSTALL_DIR}/.env"
    if [[ ! -f "$env_file" ]]; then
        ai_warn ".env not found"
        return
    fi

    while IFS= read -r line; do
        line_trimmed=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ "$line_trimmed" =~ ^# ]] && echo -e "  ${DGRN}${line_trimmed}${NC}" && continue
        [[ -z "$line_trimmed" ]] && continue
        if echo "$line_trimmed" | grep -qE "(SECRET|PASS|TOKEN|KEY)="; then
            local key
            key=$(echo "$line_trimmed" | cut -d= -f1)
            echo -e "  ${DGRN}${key}=***${NC}"
        else
            echo -e "  ${WHT}${line_trimmed}${NC}"
        fi
    done < "$env_file"
    echo ""
}

cmd_chat() {
    local message="${1:-}"
    if [[ -z "$message" ]]; then
        ai "Usage: ./dream-macos.sh chat \"your message\""
        return
    fi

    # Use jq to safely construct JSON payload (prevents injection)
    local payload
    payload=$(jq -n --arg msg "$message" \
        '{model: "default", messages: [{role: "user", content: $msg}], max_tokens: 500}')

    local response
    response=$(curl -sf -X POST "http://127.0.0.1:8080/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null) || {
        ai_err "Chat request failed."
        ai "Is llama-server running? Try: ./dream-macos.sh status"
        return
    }

    echo ""
    echo "$response" | jq -r '.choices[0].message.content // .error.message // "Error: no response"'
    echo ""
}

cmd_update() {
    test_install
    cd "$INSTALL_DIR"
    ensure_llama_cpu_budget

    # Upsert SHIELD_API_KEY when missing (pre-PR-#1069 upgrade path).
    # Without it the dashboard Privacy Shield stats panel fails after
    # update because dashboard-api can no longer authenticate its
    # proxied /stats call. Mirrors env-generator.sh upsert pattern.
    local _env_file="${INSTALL_DIR}/.env"
    if [[ -f "$_env_file" ]] && [[ -z "$(read_env_value "$_env_file" "SHIELD_API_KEY")" ]]; then
        local _shield_key
        _shield_key=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')
        upsert_env_value "$_env_file" "SHIELD_API_KEY" "$_shield_key"
    fi

    local flags
    flags=$(get_compose_flags)

    ai "Pulling latest images..."
    # shellcheck disable=SC2086
    docker compose $flags pull

    ai "Recreating containers..."
    # shellcheck disable=SC2086
    docker compose $flags up -d --force-recreate
    ai_ok "Update complete"

    sleep 5
    cmd_status
}

cmd_version() {
    echo -e "${BGRN}Dream Server v${DS_VERSION} (macOS Apple Silicon)${NC}"
}

show_help() {
    echo ""
    echo -e "  ${BGRN}Dream Server CLI (macOS)${NC}"
    echo -e "  ${DGRN}Version ${DS_VERSION}${NC}"
    echo ""
    echo -e "  ${WHT}USAGE${NC}"
    echo -e "  ${DGRN}  ./dream-macos.sh <command> [options]${NC}"
    echo ""
    echo -e "  ${WHT}COMMANDS${NC}"
    echo -e "  ${GRN}  status${NC}              ${DGRN}Health checks + Apple Silicon info${NC}"
    echo -e "  ${GRN}  start [service]${NC}     ${DGRN}Start all or one service${NC}"
    echo -e "  ${GRN}  stop [service]${NC}      ${DGRN}Stop all or one service${NC}"
    echo -e "  ${GRN}  restart [service]${NC}   ${DGRN}Restart all or one service${NC}"
    echo -e "  ${GRN}  logs <svc> [lines]${NC}  ${DGRN}Tail logs (default 100)${NC}"
    echo -e "  ${GRN}  config show${NC}         ${DGRN}View .env (secrets masked)${NC}"
    echo -e "  ${GRN}  config edit${NC}         ${DGRN}Open .env in \$EDITOR${NC}"
    echo -e "  ${GRN}  chat \"message\"${NC}      ${DGRN}Quick chat via API${NC}"
    echo -e "  ${GRN}  update${NC}              ${DGRN}Pull latest images and restart${NC}"
    echo -e "  ${GRN}  version${NC}             ${DGRN}Show version${NC}"
    echo -e "  ${GRN}  help${NC}                ${DGRN}Show this help${NC}"
    echo ""
    echo -e "  ${WHT}EXAMPLES${NC}"
    echo -e "  ${DGRN}  ./dream-macos.sh status${NC}"
    echo -e "  ${DGRN}  ./dream-macos.sh logs llama-server 50${NC}"
    echo -e "  ${DGRN}  ./dream-macos.sh restart open-webui${NC}"
    echo -e "  ${DGRN}  ./dream-macos.sh chat \"What is quantum computing?\"${NC}"
    echo ""
}

# ============================================================================
# Command Dispatch
# ============================================================================

COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
    status)     cmd_status ;;
    start)      cmd_start "${1:-}" ;;
    stop)       cmd_stop "${1:-}" ;;
    restart)    cmd_restart "${1:-}" ;;
    logs)       cmd_logs "${1:-}" "${2:-100}" ;;
    config)
        ACTION="${1:-show}"
        case "$ACTION" in
            edit)
                test_install
                ${EDITOR:-nano} "${INSTALL_DIR}/.env"
                ;;
            *)
                cmd_config_show
                ;;
        esac
        ;;
    chat)       cmd_chat "$*" ;;
    update)     cmd_update ;;
    version)    cmd_version ;;
    help)       show_help ;;
    *)
        ai_warn "Unknown command: ${COMMAND}"
        show_help
        ;;
esac
