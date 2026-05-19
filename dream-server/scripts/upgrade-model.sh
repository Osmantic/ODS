#!/bin/bash
#=============================================================================
# upgrade-model.sh — Legacy Model Directory Upgrade with Rollback
#
# Part of Dream Server — Phase 0 Foundation
#
# Legacy helper for older model-directory layouts. Current Dream Server GGUF
# installs should prefer the Dashboard Models page and docs/MODEL-MANAGEMENT.md.
#
# Usage:
#   ./upgrade-model.sh <new-model>           # Upgrade to new model
#   ./upgrade-model.sh --rollback            # Rollback to previous model
#   ./upgrade-model.sh --list                # List available models
#   ./upgrade-model.sh --current             # Show current model
#
#=============================================================================

set -euo pipefail

# Prerequisites
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed." >&2; exit 1; }

# Configuration
DREAM_DIR="${DREAM_DIR:-$HOME/dream-server}"
MODELS_DIR="${MODELS_DIR:-$DREAM_DIR/data/models}"
STATE_FILE="$DREAM_DIR/model-state.json"
BACKUP_FILE="$DREAM_DIR/model-state.backup.json"
LOG_FILE="$DREAM_DIR/upgrade-model.log"

OLLAMA_PORT="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-11434}}"
LLAMA_SERVER_CONTAINER="${LLAMA_SERVER_CONTAINER:-dream-llama-server}"

HEALTH_CHECK_TIMEOUT=120  # seconds
HEALTH_CHECK_INTERVAL=5   # seconds

INFERENCE_SERVICE="llama-server"
INFERENCE_PORT="$OLLAMA_PORT"
INFERENCE_CONTAINER="$LLAMA_SERVER_CONTAINER"
MODEL_ENV_KEY="LLM_MODEL"

detect_compose_file() {
    COMPOSE_FILE_ARGS=()
    if [[ -f "$DREAM_DIR/docker-compose.base.yml" && -f "$DREAM_DIR/docker-compose.amd.yml" ]]; then
        COMPOSE_FILE_ARGS=(-f "$DREAM_DIR/docker-compose.base.yml" -f "$DREAM_DIR/docker-compose.amd.yml")
    elif [[ -f "$DREAM_DIR/docker-compose.base.yml" && -f "$DREAM_DIR/docker-compose.nvidia.yml" ]]; then
        COMPOSE_FILE_ARGS=(-f "$DREAM_DIR/docker-compose.base.yml" -f "$DREAM_DIR/docker-compose.nvidia.yml")
    elif [[ -f "$DREAM_DIR/docker-compose.yml" ]]; then
        COMPOSE_FILE_ARGS=(-f "$DREAM_DIR/docker-compose.yml")
    fi
}

detect_inference_service() {
    if [[ ${#COMPOSE_FILE_ARGS[@]} -eq 0 ]]; then
        echo "llama-server"
        return
    fi

    if docker compose "${COMPOSE_FILE_ARGS[@]}" config --services 2>/dev/null | grep -q '^llama-server$'; then
        echo "llama-server"
    else
        echo "llama-server"
    fi
}

resolve_inference_runtime() {
    if command -v docker &> /dev/null; then
        detect_compose_file
        INFERENCE_SERVICE=$(detect_inference_service)
    else
        INFERENCE_SERVICE="llama-server"
    fi

    INFERENCE_PORT="$OLLAMA_PORT"
    INFERENCE_CONTAINER="$LLAMA_SERVER_CONTAINER"
    MODEL_ENV_KEY="LLM_MODEL"
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

#-----------------------------------------------------------------------------
# Utility Functions
#-----------------------------------------------------------------------------

log() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" >> "$LOG_FILE"
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] SUCCESS: $1"
    echo "$msg" >> "$LOG_FILE"
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $1"
    echo "$msg" >> "$LOG_FILE"
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1"
    echo "$msg" >> "$LOG_FILE"
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

ensure_dirs() {
    mkdir -p "$DREAM_DIR" "$MODELS_DIR"
    touch "$LOG_FILE"
}

#-----------------------------------------------------------------------------
# State Management
#-----------------------------------------------------------------------------

get_current_model() {
    if [[ -f "$STATE_FILE" ]]; then
        grep -o '"current": *"[^"]*"' "$STATE_FILE" | cut -d'"' -f4
    else
        echo ""
    fi
}

get_previous_model() {
    if [[ -f "$STATE_FILE" ]]; then
        grep -o '"previous": *"[^"]*"' "$STATE_FILE" | cut -d'"' -f4
    else
        echo ""
    fi
}

save_state() {
    local current="$1"
    local previous="$2"
    
    # Backup current state
    [[ -f "$STATE_FILE" ]] && cp "$STATE_FILE" "$BACKUP_FILE"
    
    jq -n         --arg cur "$current"         --arg prev "$previous"         --arg ts "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"         '{
            current: $cur,
            previous: $prev,
            updatedAt: $ts,
            history: [
                {model: $cur, activatedAt: $ts}
            ]
        }' > "$STATE_FILE"
}

#-----------------------------------------------------------------------------
# llama-server Operations
#-----------------------------------------------------------------------------

check_llm_health() {
    resolve_inference_runtime
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        "http://${LLM_HOST:-localhost}:${INFERENCE_PORT}/health" 2>/dev/null || echo "000")
    [[ "$response" == "200" ]]
}

wait_for_llm() {
    local timeout=$1
    local elapsed=0
    
    resolve_inference_runtime
    log "Waiting for ${INFERENCE_SERVICE} to be ready (timeout: ${timeout}s)..."
    
    while [[ $elapsed -lt $timeout ]]; do
        if check_llm_health; then
            success "${INFERENCE_SERVICE} is ready"
            return 0
        fi
        sleep $HEALTH_CHECK_INTERVAL
        elapsed=$((elapsed + HEALTH_CHECK_INTERVAL))
        echo -n "."
    done
    
    echo ""
    error "${INFERENCE_SERVICE} health check timed out after ${timeout}s"
    return 1
}

test_inference() {
    resolve_inference_runtime
    log "Testing inference..."

    local response
    response=$(curl -s --max-time 10 "http://${LLM_HOST:-localhost}:${INFERENCE_PORT}/v1/models" 2>/dev/null || echo "")
    
    if echo "$response" | grep -q '"data"'; then
        success "Inference test passed"
        return 0
    else
        error "Inference test failed"
        echo "$response" >> "$LOG_FILE"
        return 1
    fi
}

stop_llm() {
    resolve_inference_runtime
    log "Stopping ${INFERENCE_SERVICE}..."
    
    if command -v docker &> /dev/null; then
        if [[ ${#COMPOSE_FILE_ARGS[@]} -gt 0 ]]; then
            docker compose "${COMPOSE_FILE_ARGS[@]}" stop "$INFERENCE_SERVICE" 2>/dev/null || true
        else
            docker stop "$INFERENCE_CONTAINER" 2>/dev/null || true
            docker wait "$INFERENCE_CONTAINER" 2>/dev/null || true
        fi
    elif command -v dream &> /dev/null; then
        dream stop llama-server 2>/dev/null || true
    else
        warn "Cannot stop llama-server: no docker or dream CLI found"
        return 1
    fi
    
    success "${INFERENCE_SERVICE} stopped"
}

start_llm() {
    local model="$1"
    resolve_inference_runtime
    
    log "Starting ${INFERENCE_SERVICE} with model: $model"
    
    # Update environment or compose file
    local env_file="$DREAM_DIR/.env"
    if [[ -f "$env_file" ]]; then
        # Update active model env key for detected inference backend.
        if grep -q "^${MODEL_ENV_KEY}=" "$env_file"; then
            # Use awk index() instead of sed to avoid delimiter collisions
            awk -v k="$MODEL_ENV_KEY" -v v="$model" '{
                if (index($0, k "=") == 1) print k "=" v; else print
            }' "$env_file" > "${env_file}.tmp" && cat "${env_file}.tmp" > "$env_file" && rm -f "${env_file}.tmp"
        else
            echo "${MODEL_ENV_KEY}=$model" >> "$env_file"
        fi
    fi
    
    if command -v docker &> /dev/null; then
        # Start via docker compose (supports canonical base+overlay and legacy files)
        if [[ ${#COMPOSE_FILE_ARGS[@]} -gt 0 ]]; then
            docker compose "${COMPOSE_FILE_ARGS[@]}" up -d "$INFERENCE_SERVICE"
        else
            docker start "$INFERENCE_CONTAINER"
        fi
    elif command -v dream &> /dev/null; then
        dream start llama-server
    else
        error "Cannot start llama-server: no docker or dream CLI found"
        return 1
    fi
}

#-----------------------------------------------------------------------------
# Main Commands
#-----------------------------------------------------------------------------

cmd_list() {
    echo -e "${CYAN}Available Models:${NC}"
    echo ""
    
    if [[ -d "$MODELS_DIR" ]]; then
        for model_dir in "$MODELS_DIR"/*/; do
            if [[ -f "${model_dir}config.json" ]]; then
                local model_name size current
                model_name=$(basename "$model_dir")
                size=$(du -sh "$model_dir" 2>/dev/null | cut -f1)
                current=$(get_current_model)
                
                if [[ "$model_name" == "$current" ]]; then
                    echo -e "  ${GREEN}● $model_name${NC} ($size) [ACTIVE]"
                else
                    echo -e "  ○ $model_name ($size)"
                fi
            fi
        done
    else
        echo "  No models found in $MODELS_DIR"
    fi
    
    echo ""
    echo "For current GGUF downloads and swaps, see: docs/MODEL-MANAGEMENT.md"
}

cmd_current() {
    resolve_inference_runtime
    local current
    current=$(get_current_model)
    
    if [[ -n "$current" ]]; then
        echo -e "${CYAN}Current model:${NC} $current"
        
        if check_llm_health; then
            echo -e "${GREEN}Status:${NC} Running (${INFERENCE_SERVICE} on :${INFERENCE_PORT})"
        else
            echo -e "${RED}Status:${NC} Not responding (${INFERENCE_SERVICE} on :${INFERENCE_PORT})"
        fi
    else
        echo "No model currently configured"
    fi
}

cmd_upgrade() {
    local new_model="$1"
    
    ensure_dirs
    
    # Validate new model exists
    local model_path="$MODELS_DIR/$new_model"
    if [[ ! -d "$model_path" ]] || [[ ! -f "$model_path/config.json" ]]; then
        # Maybe it's a full path?
        if [[ -d "$new_model" ]] && [[ -f "$new_model/config.json" ]]; then
            model_path="$new_model"
            new_model=$(basename "$new_model")
        else
            error "Model not found: $new_model"
            error "Available models:"
            cmd_list
            return 1
        fi
    fi
    
    local current_model
    current_model=$(get_current_model)
    
    if [[ "$current_model" == "$new_model" ]]; then
        warn "Model $new_model is already active"
        return 0
    fi
    
    log "Upgrading model: $current_model → $new_model"
    
    # Phase 1: Stop llama-server
    echo ""
    echo -e "${CYAN}Phase 1/4:${NC} Stopping llama-server..."
    stop_llm || {
        error "Failed to stop llama-server"
        return 1
    }

    # Phase 2: Update configuration
    echo -e "${CYAN}Phase 2/4:${NC} Updating configuration..."
    save_state "$new_model" "$current_model"
    success "Configuration updated"

    # Phase 3: Start llama-server with new model
    echo -e "${CYAN}Phase 3/4:${NC} Starting llama-server with new model..."
    start_llm "$model_path" || {
        error "Failed to start llama-server"
        warn "Attempting rollback..."
        cmd_rollback
        return 1
    }
    
    # Phase 4: Health check
    echo -e "${CYAN}Phase 4/4:${NC} Verifying health..."
    if wait_for_llm $HEALTH_CHECK_TIMEOUT && test_inference; then
        echo ""
        success "Model upgrade complete!"
        echo -e "  Previous: ${YELLOW}$current_model${NC}"
        echo -e "  Current:  ${GREEN}$new_model${NC}"
        echo ""
        echo "Rollback available with: $0 --rollback"
    else
        error "Health check failed"
        warn "Attempting rollback..."
        cmd_rollback
        return 1
    fi
}

cmd_rollback() {
    local previous_model
    previous_model=$(get_previous_model)
    
    if [[ -z "$previous_model" ]]; then
        error "No previous model to rollback to"
        return 1
    fi
    
    local current_model
    current_model=$(get_current_model)
    
    log "Rolling back: $current_model → $previous_model"
    
    # Restore from backup state if available
    if [[ -f "$BACKUP_FILE" ]]; then
        cp "$BACKUP_FILE" "$STATE_FILE"
    fi
    
    local model_path="$MODELS_DIR/$previous_model"
    
    stop_llm || true
    start_llm "$model_path"
    
    if wait_for_llm $HEALTH_CHECK_TIMEOUT && test_inference; then
        success "Rollback complete"
        save_state "$previous_model" "$current_model"
    else
        error "Rollback failed - manual intervention required"
        error "Check logs: $LOG_FILE"
        return 1
    fi
}

#-----------------------------------------------------------------------------
# Entry Point
#-----------------------------------------------------------------------------

main() {
    case "${1:-}" in
        --list|-l)
            cmd_list
            ;;
        --current|-c)
            cmd_current
            ;;
        --rollback|-r)
            cmd_rollback
            ;;
        --help|-h)
            cat << EOF
Dream Server Model Upgrade

Usage:
  $0 <model-name>        Upgrade to specified model
  $0 --list              List available models
  $0 --current           Show current model
  $0 --rollback          Rollback to previous model
  $0 --help              Show this help

Note:
  This is a legacy helper for model directories containing config.json.
  Current GGUF installs should use the Dashboard Models page or
  docs/MODEL-MANAGEMENT.md.

Examples:
  $0 Qwen2.5-32B-Instruct-AWQ
  $0 /path/to/model
  $0 --rollback

Environment Variables:
  MODELS_DIR             Models directory (default: $MODELS_DIR)
  OLLAMA_PORT            llama-server port (default: 11434)
  LLAMA_SERVER_CONTAINER Docker container name (default: dream-llama-server)

EOF
            ;;
        --*)
            error "Unknown option: $1"
            exit 1
            ;;
        "")
            error "Model name required"
            echo "Usage: $0 <model-name>"
            echo "       $0 --list"
            exit 1
            ;;
        *)
            cmd_upgrade "$1"
            ;;
    esac
}

main "$@"
