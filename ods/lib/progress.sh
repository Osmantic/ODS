#!/bin/bash
# ODS — Progress Bar Utilities
# Sourced by install-core.sh for download/install progress display

# Require Bash 4+ (associative array PHASE_ESTIMATES used for phase timing)
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: $(basename "${BASH_SOURCE[0]}") requires Bash 4.0+ (current: $BASH_VERSION)" >&2
    echo "  macOS ships Bash 3.2 due to licensing. Install a modern version:" >&2
    echo "    brew install bash" >&2
    return 1 2>/dev/null || exit 1
fi

# ═══════════════════════════════════════════════════════════════
# PROGRESS BAR
# ═══════════════════════════════════════════════════════════════

# Draw a progress bar
# Usage: draw_progress_bar <current> <total> <width> <label>
draw_progress_bar() {
    local current=$1
    local total=$2
    local width=${3:-40}
    local label=${4:-"Progress"}
    
    # Guard against division by zero
    if [[ $total -le 0 ]]; then
        total=1
    fi
    
    # Calculate percentage
    local percent=$((current * 100 / total))
    
    # Calculate filled width
    local filled=$((width * current / total))
    [[ $filled -gt $width ]] && filled=$width
    local empty=$((width - filled))
    
    # Build bar
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done
    
    # Print (overwrite line)
    printf "\r  ${CYAN}%s${NC} [${GREEN}%s${NC}] %3d%%" "$label" "$bar" "$percent"
}

# Complete a progress bar with newline
complete_progress_bar() {
    local label=${1:-"Progress"}
    local width=${2:-40}
    local bar=""
    for ((i=0; i<width; i++)); do bar+="█"; done
    printf "\r  ${CYAN}%s${NC} [${GREEN}%s${NC}] 100%%\n" "$label" "$bar"
}

# ═══════════════════════════════════════════════════════════════
# TIME ESTIMATES
# ═══════════════════════════════════════════════════════════════

# Estimate download time based on size and typical speed
# Usage: estimate_download_time <size_gb> [speed_mbps]
estimate_download_time() {
    local size_gb=$1
    local speed_mbps=${2:-50}  # Assume 50 Mbps average
    
    # Convert: GB to MB, then divide by speed
    local size_mb=$((size_gb * 1024))
    local seconds=$((size_mb * 8 / speed_mbps))  # 8 bits per byte
    
    format_duration $seconds
}

# Format seconds into human-readable duration
# Usage: format_duration <seconds>
format_duration() {
    local seconds=$1
    
    if [[ $seconds -lt 60 ]]; then
        echo "${seconds}s"
    elif [[ $seconds -lt 3600 ]]; then
        local mins=$((seconds / 60))
        echo "${mins}m"
    else
        local hours=$((seconds / 3600))
        local mins=$(((seconds % 3600) / 60))
        echo "${hours}h ${mins}m"
    fi
}

# ═══════════════════════════════════════════════════════════════
# PHASE TIMING
# ═══════════════════════════════════════════════════════════════

# Estimated times per phase (in seconds) by tier
declare -A PHASE_ESTIMATES

init_phase_estimates() {
    local tier=$1
    
    case "$tier" in
        nano)
            PHASE_ESTIMATES[docker_pull]=60
            PHASE_ESTIMATES[model_download]=120
            PHASE_ESTIMATES[startup]=30
            ;;
        edge)
            PHASE_ESTIMATES[docker_pull]=90
            PHASE_ESTIMATES[model_download]=300
            PHASE_ESTIMATES[startup]=45
            ;;
        pro)
            PHASE_ESTIMATES[docker_pull]=120
            PHASE_ESTIMATES[model_download]=900
            PHASE_ESTIMATES[startup]=60
            ;;
        cluster)
            PHASE_ESTIMATES[docker_pull]=180
            PHASE_ESTIMATES[model_download]=1800
            PHASE_ESTIMATES[startup]=120
            ;;
    esac
}

# Print phase header with time estimate
# Usage: print_phase <phase_name> <description>
print_phase() {
    local phase=$1
    local desc=$2
    local estimate=${PHASE_ESTIMATES[$phase]:-0}
    local duration
    duration=$(format_duration $estimate)
    
    echo -e "\n${BOLD}${BLUE}▶ $desc${NC} ${CYAN}(~$duration)${NC}"
}

# ═══════════════════════════════════════════════════════════════
# SPINNER
# ═══════════════════════════════════════════════════════════════

SPINNER_PID=""

start_spinner() {
    local msg="${1:-Working...}"
    (
        local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
        local i=0
        while true; do
            printf "\r  ${CYAN}%s${NC} %s" "${spin:i++%10:1}" "$msg"
            sleep 0.1
        done
    ) &
    SPINNER_PID=$!
}

stop_spinner() {
    if [[ -n "$SPINNER_PID" ]]; then
        kill "$SPINNER_PID" 2>/dev/null
        wait "$SPINNER_PID" 2>/dev/null
        printf "\r"
        SPINNER_PID=""
    fi
}

# ═══════════════════════════════════════════════════════════════
# DOCKER PROGRESS WRAPPER
# ═══════════════════════════════════════════════════════════════

# Run docker compose pull with progress indication
# This wraps the native progress and adds our spinner for non-TTY
docker_pull_with_progress() {
    local compose_file=${1:-docker-compose.yml}
    
    if [[ -t 1 ]]; then
        # TTY available — let Docker show native progress
        docker compose -f "$compose_file" pull
    else
        # No TTY — use spinner
        start_spinner "Pulling Docker images..."
        docker compose -f "$compose_file" pull --quiet
        stop_spinner
    fi
}

# Monitor model download progress (for llama-server/GGUF downloads)
# Watches a directory for model files and shows progress
monitor_model_download() {
    local model_dir=$1
    local expected_size_gb=$2
    local expected_size_bytes=$((expected_size_gb * 1024 * 1024 * 1024))
    
    echo -e "  ${CYAN}Downloading model...${NC}"
    
    while true; do
        if [[ -d "$model_dir" ]]; then
            local current_bytes
            current_bytes=$(du -sb "$model_dir" 2>/dev/null | cut -f1)
            current_bytes=${current_bytes:-0}
            
            if [[ $current_bytes -ge $expected_size_bytes ]]; then
                complete_progress_bar "Model"
                break
            fi
            
            draw_progress_bar "$current_bytes" "$expected_size_bytes" 40 "Model"
        fi
        sleep 2
    done
}
