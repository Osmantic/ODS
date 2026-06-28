#!/bin/bash
# ============================================================================
# ODS Installer -- Compose Failure Report
# ============================================================================
# Part of: installers/lib/
# Purpose: Persist a bounded, shareable report when Docker Compose startup fails.
#
# Provides: write_compose_failure_report()
# ============================================================================

_ods_report_env_value() {
    local env_file="${1:-}" key="${2:-}" default="${3:-}"
    [[ -f "$env_file" ]] || { printf '%s' "$default"; return 0; }
    local value
    value="$(grep -m1 "^${key}=" "$env_file" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "${value:-$default}"
}

_ods_report_port_line() {
    local label="$1" port="$2"
    [[ -n "$port" && "$port" != "0" ]] || return 0

    local status="free"
    local detail=""
    if command -v lsof >/dev/null 2>&1; then
        detail="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -n 3 || true)"
    elif command -v ss >/dev/null 2>&1; then
        detail="$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print}' | head -n 3 || true)"
    elif command -v netstat >/dev/null 2>&1; then
        detail="$(netstat -an 2>/dev/null | awk -v p=":$port" '$0 ~ p && $0 ~ /LISTEN/ {print}' | head -n 3 || true)"
    fi
    [[ -n "$detail" ]] && status="occupied"

    printf -- "- %s:%s %s\n" "$label" "$port" "$status"
    [[ -n "$detail" ]] && printf '%s\n' "$detail" | sed 's/^/  /'
}

_ods_report_failed_images() {
    local log_file="$1"
    [[ -f "$log_file" ]] || return 0
    grep -Eio '([a-z0-9._-]+([.:][0-9]+)?/)?[a-z0-9._/-]+:[A-Za-z0-9._-]+' "$log_file" 2>/dev/null \
        | grep -E 'ghcr\.io|docker\.io|quay\.io|nvidia|llama|ods|open-webui|qdrant|speaches|comfy|litellm|perplexica' \
        | sort -u \
        | head -n 20 || true
}

_ods_report_redact_stream() {
    local env_file="${1:-}"
    awk -v env_file="$env_file" '
        BEGIN {
            secret_re = "(key|token|secret|password|pass|salt|auth|credential)"
            if (env_file != "") {
                while ((getline line < env_file) > 0) {
                    sub(/\r$/, "", line)
                    if (line !~ /^[A-Za-z_][A-Za-z0-9_]*=/) {
                        continue
                    }
                    split(line, parts, "=")
                    key = parts[1]
                    value = substr(line, length(key) + 2)
                    gsub(/^["\047]|["\047]$/, "", value)
                    if (length(value) >= 4 && tolower(key) ~ secret_re) {
                        sensitive_values[++sensitive_count] = value
                    }
                }
                close(env_file)
            }
        }
        {
            out = $0
            lowered = tolower(out)
            if (lowered ~ secret_re && match(out, /[:=][[:space:]]*/)) {
                out = substr(out, 1, RSTART + RLENGTH - 1) "[REDACTED]"
            }
            for (i = 1; i <= sensitive_count; i++) {
                value = sensitive_values[i]
                pos = index(out, value)
                while (pos > 0) {
                    out = substr(out, 1, pos - 1) "[REDACTED]" substr(out, pos + length(value))
                    pos = index(out, value)
                }
            }
            print out
        }
    '
}

write_compose_failure_report() {
    local install_dir="$1"
    local phase="$2"
    local compose_command="$3"
    local log_file="${4:-}"
    local gpu_backend="${5:-unknown}"
    local next_step="${6:-Review the failed image, Docker daemon, ports, and compose config sections below; then re-run the installer.}"

    mkdir -p "$install_dir" "$install_dir/logs" 2>/dev/null || true

    local stamp report env_file compose_flags_file
    stamp="$(date '+%Y-%m-%d-%H%M%S')"
    report="$install_dir/install-report-${stamp}.txt"
    env_file="$install_dir/.env"
    compose_flags_file="$install_dir/.compose-flags"
    local report_compose_flags="${COMPOSE_FLAGS_REPORT:-${COMPOSE_FLAGS:-}}"

    {
        echo "ODS install failure report"
        echo "Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "Phase: $phase"
        echo ""
        echo "Privacy note"
        echo "- This report avoids dumping the full .env."
        echo "- Compose config output is redacted for common secret fields and known sensitive .env values."
        echo "- Review before posting publicly."
        echo ""
        echo "Summary"
        echo "- Install dir: $install_dir"
        echo "- GPU backend: $gpu_backend"
        echo "- Compose command: $compose_command"
        [[ -n "$log_file" ]] && echo "- Installer log: $log_file"
        [[ -f "$compose_flags_file" ]] && echo "- Cached compose flags: $(cat "$compose_flags_file" 2>/dev/null)"
        echo "- Next step: $next_step"
        echo ""
        echo "Configured model/runtime"
        echo "- ODS_MODE=$(_ods_report_env_value "$env_file" ODS_MODE "unknown")"
        echo "- LLM_MODEL=$(_ods_report_env_value "$env_file" LLM_MODEL "unknown")"
        echo "- GGUF_FILE=$(_ods_report_env_value "$env_file" GGUF_FILE "unknown")"
        echo "- LLAMA_SERVER_IMAGE=$(_ods_report_env_value "$env_file" LLAMA_SERVER_IMAGE "default")"
        echo "- CTX_SIZE=$(_ods_report_env_value "$env_file" CTX_SIZE "unknown")"
        echo ""
        echo "Likely failed image(s)"
        local failed_images
        failed_images="$(_ods_report_failed_images "$log_file")"
        if [[ -n "$failed_images" ]]; then
            printf '%s\n' "$failed_images" | sed 's/^/- /'
        else
            echo "- none detected from installer log"
        fi
        echo ""
        echo "Port checks"
        _ods_report_port_line "llama-server" "$(_ods_report_env_value "$env_file" OLLAMA_PORT "11434")"
        _ods_report_port_line "open-webui" "$(_ods_report_env_value "$env_file" WEBUI_PORT "3000")"
        _ods_report_port_line "dashboard" "$(_ods_report_env_value "$env_file" DASHBOARD_PORT "3001")"
        _ods_report_port_line "dashboard-api" "$(_ods_report_env_value "$env_file" DASHBOARD_API_PORT "3002")"
        _ods_report_port_line "litellm" "$(_ods_report_env_value "$env_file" LITELLM_PORT "4000")"
        _ods_report_port_line "searxng" "$(_ods_report_env_value "$env_file" SEARXNG_PORT "8888")"
        echo ""
        echo "Docker version"
        docker version 2>&1 | sed -n '1,60p' || true
        echo ""
        echo "Docker info"
        docker info 2>&1 | sed -n '1,80p' || true
        echo ""
        echo "Compose config tail (redacted)"
        if command -v docker >/dev/null 2>&1; then
            # shellcheck disable=SC2086
            docker compose $report_compose_flags config 2>&1 | _ods_report_redact_stream "$env_file" | tail -n 80 || true
        else
            echo "docker command not found"
        fi
        echo ""
        echo "Compose ps"
        if command -v docker >/dev/null 2>&1; then
            # shellcheck disable=SC2086
            docker compose $report_compose_flags ps -a 2>&1 | sed -n '1,80p' || true
        else
            echo "docker command not found"
        fi
        echo ""
        echo "Installer log tail"
        if [[ -f "$log_file" ]]; then
            tail -n 160 "$log_file"
        else
            echo "installer log unavailable"
        fi
    } > "$report" 2>&1

    if command -v ai_warn >/dev/null 2>&1; then
        ai_warn "Compose failure report saved: $report"
    else
        echo "Compose failure report saved: $report"
    fi
    printf '%s\n' "$report"
}
