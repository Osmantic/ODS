#!/bin/bash
# ============================================================================
# Dream Server Installer -- Install Readiness Summary
# ============================================================================
# Part of: installers/lib/
# Purpose: Print a concise post-install summary showing which services are
#          ready now and which need attention.
#
# Input format for dream_readiness_summary:
#   name|health_url|container_name|open_url
#   container_name may be empty for host-native services.
# ============================================================================

_dream_readiness_http_code() {
    local url="$1" timeout="${2:-3}"
    local code
    [[ -n "$url" ]] || { printf '000'; return 0; }
    code="$(curl -s -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null || true)"
    [[ "$code" =~ ^[0-9]{3}$ ]] || code="000"
    printf '%s' "$code"
}

_dream_readiness_docker_available() {
    case "${DOCKER_CMD:-docker}" in
        "sudo docker") command -v sudo >/dev/null 2>&1 && command -v docker >/dev/null 2>&1 ;;
        *) command -v "${DOCKER_CMD:-docker}" >/dev/null 2>&1 ;;
    esac
}

_dream_readiness_docker() {
    case "${DOCKER_CMD:-docker}" in
        "sudo docker") sudo docker "$@" ;;
        *) "${DOCKER_CMD:-docker}" "$@" ;;
    esac
}

_dream_readiness_container_state() {
    local container="$1"
    [[ -n "$container" ]] || { printf 'host'; return 0; }
    if ! _dream_readiness_docker_available; then
        printf 'docker-unavailable'
        return 0
    fi
    _dream_readiness_docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || printf 'missing'
}

_dream_readiness_is_ready_code() {
    local code="$1"
    [[ "$code" =~ ^(2|3) || "$code" == "401" || "$code" == "403" ]]
}

dream_readiness_summary() {
    local status_cmd="${1:-dream status}"
    local log_file="${2:-}"
    local dashboard_url="${3:-http://localhost:3001}"

    local ready_lines=()
    local attention_lines=()
    local total=0
    local launch_record="${DREAM_COMPOSE_LAUNCH_RECORD:-}"
    if [[ -z "$launch_record" && -n "${INSTALL_DIR:-}" && -f "$INSTALL_DIR/logs/compose-launch.txt" ]]; then
        launch_record="$INSTALL_DIR/logs/compose-launch.txt"
    fi

    while IFS='|' read -r name health_url container open_url; do
        [[ -n "$name" ]] || continue
        total=$((total + 1))
        [[ -n "$open_url" ]] || open_url="$health_url"

        local http_code container_state state detail line
        http_code="$(_dream_readiness_http_code "$health_url" 3)"
        container_state="$(_dream_readiness_container_state "$container")"

        if _dream_readiness_is_ready_code "$http_code"; then
            state="ready"
            detail="HTTP $http_code"
        elif [[ "$container_state" == "running" || "$container_state" == "starting" || "$container_state" == "host" ]]; then
            state="starting"
            detail="HTTP $http_code"
        elif [[ "$container_state" == "missing" || "$container_state" == "docker-unavailable" ]]; then
            state="not detected"
            detail="$container_state"
        else
            state="needs attention"
            detail="container $container_state, HTTP $http_code"
        fi

        line=$(printf "%-28s %s (%s)" "$name" "$open_url" "$detail")
        if [[ "$state" == "ready" ]]; then
            ready_lines+=("$line")
        else
            attention_lines+=("$(printf "%-28s %s - %s" "$name" "$state" "$detail")")
        fi
    done

    [[ "$total" -gt 0 ]] || return 0

    echo ""
    echo -e "${BGRN:-}INSTALL READINESS${NC:-}"
    echo "Ready now: ${#ready_lines[@]}/${total}"
    if [[ ${#ready_lines[@]} -gt 0 ]]; then
        echo "Ready:"
        for line in "${ready_lines[@]}"; do
            echo "  [OK] $line"
        done
    fi

    if [[ ${#attention_lines[@]} -gt 0 ]]; then
        echo "Needs attention:"
        for line in "${attention_lines[@]}"; do
            echo "  [!!] $line"
        done
    fi

    echo "Next:"
    echo "  - Open dashboard: $dashboard_url"
    echo "  - Check status: $status_cmd"
    [[ -n "$log_file" ]] && echo "  - Logs: $log_file"
    if [[ -n "$launch_record" ]]; then
        echo "  - Compose launch: $launch_record"
        if [[ -f "$launch_record" ]]; then
            local compose_logs_cmd
            compose_logs_cmd="$(sed -n 's/^compose_logs_command=//p' "$launch_record" 2>/dev/null | head -n 1 || true)"
            [[ -n "$compose_logs_cmd" ]] && echo "  - Compose logs: $compose_logs_cmd"
        fi
    fi
    echo ""
}
