#!/bin/bash
# Dream Server Comprehensive Health Check
# Tests each component with actual API calls, not just connectivity
# Exit codes: 0=healthy, 1=degraded (some services down), 2=critical (core services down)
#
# Usage: ./health-check.sh [--json] [--quiet]

# ── Bash 4+ guard ─────────────────────────────────────────────────────────────
# service-registry.sh requires associative arrays (declare -A) which need Bash 4+.
# macOS ships Bash 3.2; if running there, re-exec under Homebrew bash.
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _brew_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [ -x "$_brew_bash" ] && [ "$("$_brew_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_brew_bash" "$0" "$@"
        fi
    done
    echo "Error: Bash 4+ required. macOS ships Bash 3.2. Install newer bash: brew install bash" >&2
    exit 2
fi

set -euo pipefail

# Parse args
JSON_OUTPUT=false
QUIET=false
for arg in "$@"; do
    case $arg in
        --json) JSON_OUTPUT=true ;;
        --quiet) QUIET=true ;;
    esac
done

# Config (defaults; .env overrides after load_env_file below)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" 
if [[ -f "$SCRIPT_DIR/lib/service-registry.sh" ]]; then 
    . "$SCRIPT_DIR/lib/service-registry.sh" 
    sr_load 
fi
INSTALL_DIR="${INSTALL_DIR:-$HOME/dream-server}"
LLM_HOST="${LLM_HOST:-localhost}"
LLM_PORT="${LLM_PORT:-8080}"
TIMEOUT="${TIMEOUT:-5}"

# Safe .env loading for port overrides (no eval; use lib/safe-env.sh)
[[ -f "$SCRIPT_DIR/lib/safe-env.sh" ]] && . "$SCRIPT_DIR/lib/safe-env.sh"
load_env_file "${INSTALL_DIR}/.env"
sr_resolve_ports

# Colors (disabled for JSON/quiet)
if $JSON_OUTPUT || $QUIET; then
    GREEN="" RED="" YELLOW="" CYAN="" NC=""
else
    GREEN='\033[0;32m' RED='\033[0;31m' YELLOW='\033[1;33m' CYAN='\033[0;36m' NC='\033[0m'
fi

# Track results (indexed arrays — Bash 3.2 compatible as defense-in-depth)
declare -a RESULT_KEYS=()
declare -a RESULT_VALS=()
CRITICAL_FAIL=false
ANY_FAIL=false

# Set a result: result_set key value
result_set() {
    local key="$1" val="$2" i
    for i in "${!RESULT_KEYS[@]}"; do
        if [[ "${RESULT_KEYS[$i]}" == "$key" ]]; then
            RESULT_VALS[i]="$val"
            return
        fi
    done
    RESULT_KEYS+=("$key")
    RESULT_VALS+=("$val")
}

# Get a result: result_get key
result_get() {
    local key="$1" i
    for i in "${!RESULT_KEYS[@]}"; do
        if [[ "${RESULT_KEYS[$i]}" == "$key" ]]; then
            echo "${RESULT_VALS[$i]}"
            return
        fi
    done
}

log() { $QUIET || echo -e "$1"; }

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

# ── Test functions ──────────────────────────────────────────────────────────

# llama-server: critical path — performs an actual inference test
test_llm() {
    local start
    start=$(_now_ms)
    local response
    response=$(curl -sf --max-time $TIMEOUT \
        -H "Content-Type: application/json" \
        -d '{"model":"default","prompt":"Hi","max_tokens":1}' \
        "http://${LLM_HOST}:${LLM_PORT}/v1/completions" 2>/dev/null)
    local end
    end=$(_now_ms)

    if echo "$response" | grep -q '"text"'; then
        result_set "llm" "ok"
        result_set "llm_latency" "$((end - start))"
        return 0
    fi
    result_set "llm" "fail"
    CRITICAL_FAIL=true
    ANY_FAIL=true
    return 1
}

# Check Docker container state for a service
# Returns: container state string (or empty if docker unavailable/container name missing)
check_container_state() {
    local sid="$1"
    local container="${SERVICE_CONTAINERS[$sid]}"

    # Guard: empty container name
    [[ -z "$container" ]] && return 0

    # Skip if docker not available
    if ! command -v docker &>/dev/null; then
        return 0
    fi

    # Get container state via docker inspect
    local state
    state=$(docker inspect --format '{{.State.Status}}' "$container" 2>&1)
    local inspect_exit=$?

    if [[ $inspect_exit -ne 0 ]]; then
        echo "not_found"
        return 1
    elif [[ "$state" == "running" ]]; then
        echo "running"
        return 0
    elif [[ "$state" == "restarting" ]]; then
        echo "restarting"
        return 1
    else
        # exited, paused, dead, created
        echo "$state"
        return 1
    fi
}

# Generic registry-driven service health check
test_service() {
    local sid="$1"
    local port_env="${SERVICE_PORT_ENVS[$sid]}"
    local default_port="${SERVICE_PORTS[$sid]}"
    local health="${SERVICE_HEALTH[$sid]}"
    local health_type="${SERVICE_HEALTH_TYPES[$sid]:-http}"
    local timeout="${SERVICE_HEALTH_TIMEOUTS[$sid]:-$TIMEOUT}"

    # Resolve port
    local port="$default_port"
    [[ -n "$port_env" ]] && port="${!port_env:-$default_port}"

    if [[ "$health_type" == "none" ]]; then
        result_set "$sid" "ok"
        return 0
    fi

    if [[ "$health_type" == "http" ]] && [[ -z "$health" || "$port" == "0" ]]; then
        return 1
    fi
    if [[ "$health_type" == "tcp" ]] && [[ "$port" == "0" ]]; then
        return 1
    fi

    # Check container state first (if docker available)
    local container_state
    container_state=$(check_container_state "$sid")
    if [[ -n "$container_state" && "$container_state" != "running" ]]; then
        result_set "$sid" "fail"
        ANY_FAIL=true
        return 1
    fi

    if [[ "$health_type" == "tcp" ]]; then
        # TCP connection test using Python socket
        if python3 -c "import socket; s = socket.create_connection(('127.0.0.1', $port), timeout=$timeout); s.close()" 2>/dev/null; then
            result_set "$sid" "ok"
            return 0
        fi
    else
        # HTTP GET test
        if curl -sf --max-time "$timeout" "http://127.0.0.1:${port}${health}" >/dev/null 2>&1; then
            result_set "$sid" "ok"
            return 0
        fi
    fi

    result_set "$sid" "fail"
    ANY_FAIL=true
    return 1
}

# System-level: GPU
test_gpu() {
    if command -v nvidia-smi &>/dev/null; then
        local gpu_info
        gpu_info=$(nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
        if [ -n "$gpu_info" ]; then
            IFS=',' read -r mem_used mem_total gpu_util temp <<< "$gpu_info"
            result_set "gpu" "ok"
            result_set "gpu_mem_used" "${mem_used// /}"
            result_set "gpu_mem_total" "${mem_total// /}"
            result_set "gpu_util" "${gpu_util// /}"
            result_set "gpu_temp" "${temp// /}"

            # Warn if GPU memory > 95% or temp > 80C
            if [ "$(result_get "gpu_util")" -gt 95 ] 2>/dev/null; then
                result_set "gpu" "warn"
            fi
            if [ "$(result_get "gpu_temp")" -gt 80 ] 2>/dev/null; then
                result_set "gpu" "warn"
            fi
            return 0
        fi
    fi
    result_set "gpu" "unavailable"
    return 1
}

# System-level: Disk
test_disk() {
    local usage
    usage=$(df -h "$INSTALL_DIR" 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
    if [ -n "$usage" ]; then
        result_set "disk" "ok"
        result_set "disk_usage" "$usage"
        if [ "$usage" -gt 90 ]; then
            result_set "disk" "warn"
        fi
        return 0
    fi
    result_set "disk" "unavailable"
    return 1
}

# Helper: run test_service for a service ID and log the result
check_service() {
    local sid="$1"
    local name="${SERVICE_NAMES[$sid]:-$sid}"
    if test_service "$sid" 2>/dev/null; then
        log "  ${GREEN}✓${NC} $name - healthy"
        return 0
    else
        log "  ${YELLOW}!${NC} $name - not responding"
        return 1
    fi
}

# Helper: run test_service in background and store result in temp file
check_service_async() {
    local sid="$1"
    local result_file="$2"

    # Check container state first
    local container_state
    container_state=$(check_container_state "$sid")

    if test_service "$sid" 2>/dev/null; then
        echo "ok:$sid:$container_state" > "$result_file"
    else
        echo "fail:$sid:$container_state" > "$result_file"
    fi
}

# ── Run tests ───────────────────────────────────────────────────────────────

# Create temp dir for parallel results
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

log "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
log "${CYAN}  Dream Server Health Check${NC}"
log "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
log ""

log "${CYAN}Core Services:${NC}"

# llama-server (critical — does inference test, not just health)
if test_llm 2>/dev/null; then
    log "  ${GREEN}✓${NC} llama-server - inference working ($(result_get "llm_latency")ms)"
else
    log "  ${RED}✗${NC} llama-server - CRITICAL: inference failed"
fi

# Launch all other core services in parallel
declare -a CORE_PIDS=()
declare -a CORE_SIDS=()
for sid in "${SERVICE_IDS[@]}"; do
    [[ "$sid" == "llama-server" ]] && continue
    [[ "${SERVICE_CATEGORIES[$sid]}" != "core" ]] && continue
    result_file="$TEMP_DIR/core_$sid"
    check_service_async "$sid" "$result_file" &
    CORE_PIDS+=($!)
    CORE_SIDS+=("$sid")
done

# Wait for all core service checks to complete
for pid in "${CORE_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Display core service results
for sid in "${CORE_SIDS[@]}"; do
    result_file="$TEMP_DIR/core_$sid"
    if [[ -f "$result_file" ]]; then
        result=$(cat "$result_file")
        name="${SERVICE_NAMES[$sid]:-$sid}"

        # Parse result format: status:sid:container_state
        IFS=':' read -r status sid_check container_state <<< "$result"

        if [[ "$status" == "ok" ]]; then
            log "  ${GREEN}✓${NC} $name - healthy"
        else
            # Use container state for better error message
            case "$container_state" in
                not_found)
                    log "  ${YELLOW}!${NC} $name - container not found"
                    ;;
                exited|stopped)
                    log "  ${YELLOW}!${NC} $name - container stopped"
                    ;;
                restarting)
                    log "  ${YELLOW}!${NC} $name - container restarting"
                    ;;
                running)
                    log "  ${YELLOW}!${NC} $name - not responding (container running)"
                    ;;
                *)
                    log "  ${YELLOW}!${NC} $name - not responding"
                    ;;
            esac
        fi
    fi
done

log ""
log "${CYAN}Extension Services:${NC}"

# Launch all extension services in parallel
declare -a EXT_PIDS=()
declare -a EXT_SIDS=()
for sid in "${SERVICE_IDS[@]}"; do
    [[ "${SERVICE_CATEGORIES[$sid]}" == "core" ]] && continue
    result_file="$TEMP_DIR/ext_$sid"
    check_service_async "$sid" "$result_file" &
    EXT_PIDS+=($!)
    EXT_SIDS+=("$sid")
done

# Wait for all extension service checks to complete
for pid in "${EXT_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Display extension service results
for sid in "${EXT_SIDS[@]}"; do
    result_file="$TEMP_DIR/ext_$sid"
    if [[ -f "$result_file" ]]; then
        result=$(cat "$result_file")
        name="${SERVICE_NAMES[$sid]:-$sid}"

        # Parse result format: status:sid:container_state
        IFS=':' read -r status sid_check container_state <<< "$result"

        if [[ "$status" == "ok" ]]; then
            log "  ${GREEN}✓${NC} $name - healthy"
        else
            # Use container state for better error message
            case "$container_state" in
                not_found)
                    log "  ${YELLOW}!${NC} $name - container not found"
                    ;;
                exited|stopped)
                    log "  ${YELLOW}!${NC} $name - container stopped"
                    ;;
                restarting)
                    log "  ${YELLOW}!${NC} $name - container restarting"
                    ;;
                running)
                    log "  ${YELLOW}!${NC} $name - not responding (container running)"
                    ;;
                *)
                    log "  ${YELLOW}!${NC} $name - not responding"
                    ;;
            esac
        fi
    fi
done

log ""
log "${CYAN}System Resources:${NC}"

# GPU
if test_gpu 2>/dev/null; then
    status_icon="${GREEN}✓${NC}"
    [ "$(result_get "gpu")" = "warn" ] && status_icon="${YELLOW}!${NC}"
    log "  ${status_icon} GPU - $(result_get "gpu_mem_used")/$(result_get "gpu_mem_total") MiB, $(result_get "gpu_util")% util, $(result_get "gpu_temp")°C"
else
    log "  ${YELLOW}?${NC} GPU - status unavailable"
fi

# Disk
if test_disk 2>/dev/null; then
    status_icon="${GREEN}✓${NC}"
    [ "$(result_get "disk")" = "warn" ] && status_icon="${YELLOW}!${NC}"
    log "  ${status_icon} Disk - $(result_get "disk_usage")% used"
else
    log "  ${YELLOW}?${NC} Disk - status unavailable"
fi

log ""

# Summary
if $CRITICAL_FAIL; then
    log "${RED}Status: CRITICAL - Core services down${NC}"
    EXIT_CODE=2
elif $ANY_FAIL; then
    log "${YELLOW}Status: DEGRADED - Some services unavailable${NC}"
    EXIT_CODE=1
else
    log "${GREEN}Status: HEALTHY - All services operational${NC}"
    EXIT_CODE=0
fi

log ""

# JSON output
if $JSON_OUTPUT; then
    echo "{"
    echo "  \"timestamp\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\","
    echo "  \"status\": \"$([ $EXIT_CODE -eq 0 ] && echo "healthy" || ([ $EXIT_CODE -eq 1 ] && echo "degraded" || echo "critical"))\","
    echo "  \"services\": {"
    first=true
    for i in "${!RESULT_KEYS[@]}"; do
        $first || echo ","
        first=false
        echo -n "    \"${RESULT_KEYS[$i]}\": \"${RESULT_VALS[$i]}\""
    done
    echo ""
    echo "  }"
    echo "}"
fi

exit $EXIT_CODE
