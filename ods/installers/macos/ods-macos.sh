#!/bin/bash
# ============================================================================
# ODS macOS CLI -- ods-macos.sh
# ============================================================================
# Day-to-day management of a ODS installation on macOS.
# Mirrors the Windows ods.ps1 command structure.
#
# Usage:
#   ./ods-macos.sh status              # Health checks + Apple Silicon info
#   ./ods-macos.sh start [service]     # Start all or one service
#   ./ods-macos.sh stop [service]      # Stop all or one service
#   ./ods-macos.sh restart [service]   # Restart all or one service
#   ./ods-macos.sh logs <service> [N]  # Tail logs (default 100 lines)
#   ./ods-macos.sh config show         # View .env (secrets masked)
#   ./ods-macos.sh config edit         # Open .env in $EDITOR
#   ./ods-macos.sh chat "message"      # Quick chat via API
#   ./ods-macos.sh update              # Pull latest images and restart
#   ./ods-macos.sh version             # Show version
#   ./ods-macos.sh help                # Show help
#
# ============================================================================

# Guard: macOS ships Bash 3.2 (GPL). ods-cli and our libs need Bash 4+.
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
    echo "ODS requires Bash 4+ (you have ${BASH_VERSION})." >&2
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
# Lets `bash /path/to/install/ods-macos.sh status` work without ODS_HOME
# when /path/to/install contains an installer-generated .env sentinel. Unset
# after the sourced chain so the hint does not leak into child processes we
# spawn later (docker compose, curl, etc.).
export ODS_SCRIPT_HINT="$SCRIPT_DIR"

# Source only what we need for CLI
source "${LIB_DIR}/constants.sh"
source "${LIB_DIR}/ui.sh"
source "${LIB_DIR}/bridge-manager.sh"
source "${LIB_DIR}/native-model.sh"
source "${LIB_DIR}/detection.sh"

unset ODS_SCRIPT_HINT

# ── Resolve install directory ──
INSTALL_DIR="${ODS_INSTALL_DIR}"

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

# Install-directory checks only. Commands that read or edit local files
# (config show / config edit) use this so they keep working while the Docker
# runtime is down -- which is exactly when a user needs to look at .env.
test_install_dir() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        ai_err "ODS not found at ${INSTALL_DIR}."
        ai "Invoke from inside the install dir (bash <install>/ods-macos.sh status), export ODS_HOME=<install>, or run the installer."
        exit 1
    fi
    local base_compose="${INSTALL_DIR}/docker-compose.base.yml"
    local mono_compose="${INSTALL_DIR}/docker-compose.yml"
    if [[ ! -f "$base_compose" ]] && [[ ! -f "$mono_compose" ]]; then
        ai_err "docker-compose.base.yml not found in ${INSTALL_DIR}"
        exit 1
    fi
}

# Install directory plus a reachable Docker runtime, for commands that talk
# to compose.
test_install() {
    test_install_dir
    test_docker_running || exit 1
}

get_compose_flags() {
    local flags helper
    flags="$(_get_base_compose_flags)" || return $?
    helper="${INSTALL_DIR}/installers/macos/lib/pixel-native-stack.py"
    if [[ -e "${INSTALL_DIR}/data/pixel-native/preparation/activation.json" || -L "${INSTALL_DIR}/data/pixel-native/preparation/activation.json" ]]; then
        /usr/bin/python3 "$helper" --install-dir "$INSTALL_DIR" --flags="$flags"
    else
        printf '%s\n' "$flags"
    fi
}

_get_base_compose_flags() {
    ensure_hermes_dashboard_session_token

    local flags_file="${INSTALL_DIR}/.compose-flags"
    if [[ -f "$flags_file" ]]; then
        macos_model_store_compose_flags "$(cat "$flags_file")"
        return $?
    fi
    # Fallback: dynamic resolution via resolve-compose-stack.sh so user-installed
    # extensions in data/user-extensions/ are discovered when the .compose-flags
    # cache is missing or stale. Mirrors ods-cli's get_compose_flags fallback.
    local ods_mode
    ods_mode="$(read_env_value "${INSTALL_DIR}/.env" "ODS_MODE")"
    ods_mode="${ods_mode#\"}"
    ods_mode="${ods_mode%\"}"
    ods_mode="${ods_mode#\'}"
    ods_mode="${ods_mode%\'}"
    [[ -n "$ods_mode" ]] || ods_mode="local"
    if [[ -x "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" ]]; then
        # Pass --gpu-count for parity with the Linux paths even though there's
        # currently no docker-compose.multigpu-apple.yml — keeps the contract
        # uniform across all resolver call sites.
        "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "${TIER:-1}" \
            --gpu-backend "${GPU_BACKEND:-apple}" \
            --gpu-count "${GPU_COUNT:-1}" \
            --ods-mode "$ods_mode"
        return
    fi
    # Last resort: preserve cloud/local overlay selection on older installs.
    local flags="-f docker-compose.base.yml"
    if [[ "$ods_mode" == "cloud" ]] && [[ -f "${INSTALL_DIR}/docker-compose.cloud.yml" ]]; then
        flags="$flags -f docker-compose.cloud.yml"
    elif [[ -f "${INSTALL_DIR}/installers/macos/docker-compose.macos.yml" ]]; then
        flags="$flags -f installers/macos/docker-compose.macos.yml"
    fi
    macos_model_store_compose_flags "$flags"
}

compose_pull_with_retry() {
    local flags="$1"
    local -a pull_services=()
    if [[ -f "${INSTALL_DIR}/data/pixel-native/preparation/activation.json" ]]; then
        local image actual services service found=false
        image="$(read_env_value "${INSTALL_DIR}/.env" PIXEL_NATIVE_INGRESS_IMAGE)"
        if [[ ! "$image" =~ ^sha256:[a-f0-9]{64}$ ]]; then
            ai_err "Native Pixel ingress image identity is missing; retain its installation receipts."
            return 1
        fi
        actual="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null)" || actual=""
        if [[ "$actual" != "$image" ]]; then
            ai_err "The pinned native Pixel ingress image is unavailable locally; recover it before updating."
            return 1
        fi
        # A local image ID is not a registry reference. Keep the verified native
        # transport image while pulling the remaining updatable services.
        # shellcheck disable=SC2086
        services="$(docker compose $flags config --services)" || return 1
        while IFS= read -r service; do
            if [[ "$service" == pixel-native-ingress ]]; then
                found=true
            elif [[ "$service" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
                pull_services+=("$service")
            else
                ai_err "Invalid Compose service selection."
                return 1
            fi
        done <<< "$services"
        if ! $found || [[ ${#pull_services[@]} -eq 0 ]]; then
            ai_err "Native Pixel Compose selection is incomplete."
            return 1
        fi
    fi
    local log_file
    log_file="$(mktemp)"
    local max_attempts="${ODS_COMPOSE_PULL_RETRY_ATTEMPTS:-3}"
    if ! [[ "$max_attempts" =~ ^[0-9]+$ ]] || (( max_attempts < 1 )); then
        max_attempts=3
    fi

    local attempt=1 rc=0
    while :; do
        : > "$log_file"
        rc=0
        # shellcheck disable=SC2086
        docker compose $flags pull --ignore-buildable "${pull_services[@]}" >"$log_file" 2>&1 || rc=$?
        if (( rc == 0 )); then
            rm -f "$log_file"
            return 0
        fi

        if (( attempt >= max_attempts )) || ! grep -Eiq 'context deadline exceeded|i/o timeout|TLS handshake timeout|connection reset by peer|connection timed out|temporary failure|network is unreachable|net/http: request canceled|unexpected EOF|failed to authorize: failed to fetch' "$log_file"; then
            ai_err "docker compose pull failed (exit code: ${rc})"
            tail -40 "$log_file" >&2 || true
            rm -f "$log_file"
            return "$rc"
        fi

        local delay
        case "$attempt" in
            1) delay="${ODS_COMPOSE_PULL_RETRY_DELAY_1:-5}" ;;
            2) delay="${ODS_COMPOSE_PULL_RETRY_DELAY_2:-15}" ;;
            *) delay="${ODS_COMPOSE_PULL_RETRY_DELAY_N:-30}" ;;
        esac
        ai_warn "Docker registry pull hit a transient network error; retrying (${attempt}/$((max_attempts - 1)))."
        sleep "$delay"
        attempt=$((attempt + 1))
    done
}

read_ods_env() {
    local env_file="${INSTALL_DIR}/.env"
    if [[ ! -f "$env_file" ]]; then
        return
    fi
    # Parse .env safely (no eval). Keep a last line that has no newline.
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ "$line" =~ ^# ]] && continue
        [[ -z "$line" ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"
            # Apply Docker Compose's value grammar, mirrored from
            # lib/safe-env.sh, before stripping quotes: trim surrounding
            # whitespace (Compose trims leading space, so "KEY=  # x" becomes
            # the literal "# x"), then for an unquoted value cut at the first
            # " #", and for a quoted value drop a " #..." after the closing
            # quote. "#" without a leading space and "#" inside quotes stay.
            val="${val#"${val%%[![:space:]]*}"}"
            val="${val%"${val##*[![:space:]]}"}"
            case "$val" in
                \"*) [[ "$val" =~ ^(\"(\\.|[^\"\\])*\")[[:space:]]+# ]] && val="${BASH_REMATCH[1]}" ;;
                \'*) [[ "$val" =~ ^(\'[^\']*\')[[:space:]]+# ]] && val="${BASH_REMATCH[1]}" ;;
                *)
                    val="${val%% #*}"
                    val="${val%"${val##*[![:space:]]}"}"
                    ;;
            esac
            # Strip exactly one matching pair of surrounding quotes. The old
            # sed removed a leading and a trailing quote independently (either
            # type), so KEY=abc" lost its trailing quote and "abc' was cut on
            # both ends. Mismatched quotes stay verbatim, matching
            # lib/safe-env.sh used by the Linux CLI.
            if [[ "$val" == '"'*'"' ]]; then
                val="${val#\"}"
                val="${val%\"}"
                # Decode writer escapes without evaluating shell expansions.
                # Single-quoted values below remain literal.
                val="${val//\\\"/\"}"
                val="${val//\\\$/\$}"
                val="${val//\\\\/\\}"
            elif [[ "$val" == "'"*"'" ]]; then
                val="${val#\'}"
                val="${val%\'}"
            fi
            export "ENV_${key}=${val}"
        fi
    done < "$env_file"
}

macos_bootstrap_status() {
    local status_file="${INSTALL_DIR}/data/bootstrap-status.json"
    [[ -f "$status_file" ]] || return 0
    grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "$status_file" \
        | sed -n '1p' \
        | sed 's/.*"status"[[:space:]]*:[[:space:]]*"//' \
        | sed 's/"//' \
        || true
}

macos_wait_for_bootstrap_compose_safe() {
    local action="${1:-service operation}"
    local status_file="${INSTALL_DIR}/data/bootstrap-status.json"
    [[ -f "$status_file" ]] || return 0

    local max_wait="${ODS_MACOS_BOOTSTRAP_COMPOSE_WAIT_SECONDS:-900}"
    local interval="${ODS_MACOS_BOOTSTRAP_COMPOSE_WAIT_INTERVAL:-5}"
    if ! [[ "$max_wait" =~ ^[0-9]+$ ]] || (( max_wait < 0 )); then
        max_wait=900
    fi
    if ! [[ "$interval" =~ ^[0-9]+$ ]] || (( interval < 1 )); then
        interval=5
    fi

    local waited=0 announced=false status=""
    while true; do
        status="$(macos_bootstrap_status)"
        case "$status" in
            starting|verifying|swapping)
                if [[ "$announced" == "false" ]]; then
                    ai "Model upgrade is ${status}; waiting before ${action} touches llama-server..."
                    announced=true
                fi
                if (( waited >= max_wait )); then
                    ai_warn "Model upgrade is still ${status} after ${max_wait}s; refusing to run ${action} against an active hot-swap."
                    return 1
                fi
                sleep "$interval"
                waited=$(( waited + interval ))
                ;;
            *)
                if [[ "$announced" == "true" ]]; then
                    ai "Model upgrade is ${status:-idle}; continuing with ${action}."
                fi
                return 0
                ;;
        esac
    done
}

macos_launch_detached_bootstrap_upgrade() {
    local upgrade_script="$1"
    shift
    local pid_file="${INSTALL_DIR}/data/bootstrap-upgrade.pid"
    local log_file="${INSTALL_DIR}/logs/model-upgrade.log"
    local python_cmd="${PYTHON_CMD:-/usr/bin/python3}"
    local bash_cmd="${BASH:-bash}"
    [[ -x "$python_cmd" ]] || python_cmd="$(command -v python3 || command -v python || true)"
    [[ -n "$python_cmd" ]] || {
        ai_warn "Python is unavailable; cannot launch background model-upgrade retry."
        return 1
    }
    if command -v cygpath >/dev/null 2>&1; then
        bash_cmd="$(cygpath -w "$bash_cmd" 2>/dev/null || printf '%s' "$bash_cmd")"
    fi

    BOOTSTRAP_BASH="$bash_cmd" "$python_cmd" - "$pid_file" "$log_file" "$upgrade_script" "$@" <<'BOOTSTRAP_LAUNCH_PY'
import os
import subprocess
import sys
from pathlib import Path

pid_path = Path(sys.argv[1])
log_path = Path(sys.argv[2])
script = sys.argv[3]
script_args = sys.argv[4:]
if not script_args:
    raise SystemExit("bootstrap launcher requires the install directory")
bash_exe = os.environ.get("BOOTSTRAP_BASH") or os.environ.get("BASH") or "bash"
log_path.parent.mkdir(parents=True, exist_ok=True)
pid_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("ab", buffering=0) as log_handle:
    proc = subprocess.Popen(
        [bash_exe, script, *script_args],
        cwd=script_args[0],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
tmp = pid_path.with_name(f"{pid_path.name}.{os.getpid()}.tmp")
tmp.write_text(f"{proc.pid}\n", encoding="ascii")
os.chmod(tmp, 0o600)
os.replace(tmp, pid_path)
BOOTSTRAP_LAUNCH_PY
}

macos_maybe_resume_bootstrap_upgrade() {
    local status_file="${INSTALL_DIR}/data/bootstrap-status.json"
    local args_file="${INSTALL_DIR}/data/bootstrap-upgrade.args"
    local upgrade_script="${INSTALL_DIR}/scripts/bootstrap-upgrade.sh"
    [[ -f "$status_file" && -f "$args_file" && -f "$upgrade_script" ]] || return 0

    local bs_status
    bs_status="$(macos_bootstrap_status)"
    [[ "$bs_status" == "failed" ]] || return 0

    if command -v pgrep >/dev/null 2>&1 && pgrep -f "[/]bootstrap-upgrade[.]sh.*${INSTALL_DIR}" >/dev/null 2>&1; then
        ai "Model upgrade retry is already running."
        return 0
    fi

    local full_gguf_file full_gguf_url full_gguf_sha256 full_llm_model full_max_context bootstrap_gguf_file
    full_gguf_file="$(sed -n '1p' "$args_file")"
    full_gguf_url="$(sed -n '2p' "$args_file")"
    full_gguf_sha256="$(sed -n '3p' "$args_file")"
    full_llm_model="$(sed -n '4p' "$args_file")"
    full_max_context="$(sed -n '5p' "$args_file")"
    bootstrap_gguf_file="$(sed -n '6p' "$args_file")"

    if [[ -z "$full_gguf_file" || -z "$full_gguf_url" || -z "$full_llm_model" || -z "$full_max_context" || -z "$bootstrap_gguf_file" ]]; then
        ai_warn "Bootstrap model upgrade failed previously, but retry metadata is incomplete."
        return 0
    fi

    if macos_launch_detached_bootstrap_upgrade "$upgrade_script" \
        "$INSTALL_DIR" "$full_gguf_file" "$full_gguf_url" \
        "$full_gguf_sha256" "$full_llm_model" "$full_max_context" \
        "$bootstrap_gguf_file"; then
        ai "Model upgrade failed previously; retrying in background (${full_llm_model})."
        ai "Check progress: tail -f ${INSTALL_DIR}/logs/model-upgrade.log"
    else
        ai_warn "Could not relaunch the failed background model upgrade."
    fi
}

resolve_cli_llm_route() {
    read_ods_env

    CLI_LLM_MODE="${ENV_ODS_MODE:-local}"
    CLI_LLM_API_KEY=""
    if [[ "$CLI_LLM_MODE" == "cloud" ]]; then
        local litellm_port="${ENV_LITELLM_PORT:-4000}"
        local cloud_bind_address="${ENV_BIND_ADDRESS:-127.0.0.1}"
        local cloud_probe_host
        [[ "$litellm_port" =~ ^[0-9]+$ ]] || litellm_port="4000"
        cloud_probe_host="$(macos_bind_probe_host "$cloud_bind_address")"
        CLI_LLM_NAME="LLM API (LiteLLM)"
        CLI_LLM_BASE_URL="http://${cloud_probe_host}:${litellm_port}"
        # This endpoint verifies both gateway readiness and its master key.
        CLI_LLM_HEALTH_URL="${CLI_LLM_BASE_URL}/v1/models"
        CLI_LLM_API_KEY="${ENV_LITELLM_KEY:-}"
        return
    fi

    local native_port="${ENV_ODS_NATIVE_LLAMA_PORT:-${ENV_OLLAMA_PORT:-8080}}"
    [[ "$native_port" =~ ^[0-9]+$ ]] || native_port="8080"
    local bind_address="${ENV_BIND_ADDRESS:-127.0.0.1}"
    local probe_host
    probe_host="$(macos_bind_probe_host "$bind_address")"
    CLI_LLM_NAME="LLM API"
    CLI_LLM_BASE_URL="http://${probe_host}:${native_port}"
    CLI_LLM_HEALTH_URL="${CLI_LLM_BASE_URL}/health"
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
        # Appending after a last line that has no newline would join the new
        # assignment onto that line and corrupt both keys.
        if [[ -s "$env_file" && -n "$(tail -c 1 "$env_file")" ]]; then
            printf '\n' >> "$env_file"
        fi
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

ensure_hermes_dashboard_session_token() {
    local env_file="${INSTALL_DIR}/.env"
    [[ -f "$env_file" ]] || return 0
    [[ -n "$(read_env_value "$env_file" "HERMES_DASHBOARD_SESSION_TOKEN")" ]] && return 0

    local token
    if command -v openssl >/dev/null 2>&1; then
        token="$(openssl rand -hex 32)"
    else
        token="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    [[ "$token" =~ ^[0-9a-f]{64}$ ]] || {
        ai_err "Could not generate HERMES_DASHBOARD_SESSION_TOKEN"
        return 1
    }
    upsert_env_value "$env_file" "HERMES_DASHBOARD_SESSION_TOKEN" "$token"
}

proxy_is_enabled() {
    [[ -f "${INSTALL_DIR}/extensions/services/ods-proxy/compose.yaml" ]] \
        || [[ -f "${INSTALL_DIR}/data/user-extensions/ods-proxy/compose.yaml" ]]
}

require_proxy_auth() {
    local env_file="${INSTALL_DIR}/.env"
    [[ -f "$env_file" ]] || {
        ai_err "Cannot enable network access without ${env_file}."
        return 1
    }
    if [[ "$(read_env_value "$env_file" "WEBUI_AUTH")" != "true" ]]; then
        upsert_env_value "$env_file" "WEBUI_AUTH" "true"
        ai "Network access requires sign-in; set WEBUI_AUTH=true."
    fi
    export WEBUI_AUTH=true
}

prepare_proxy_start() {
    local flags="$1"
    require_proxy_auth || return 1
    ai "Applying authenticated Open WebUI configuration..."
    # shellcheck disable=SC2086
    docker compose $flags up -d --no-deps --force-recreate open-webui
}

select_auto_cpu_value() {
    local existing="$1"
    local detected="$2"
    if [[ "$existing" =~ ^[0-9]+([.][0-9]+)?$ ]] && LC_ALL=C awk "BEGIN { exit !($existing > 0 && $existing <= $detected) }"; then
        echo "$existing"
    else
        echo "$detected"
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

ensure_service_cpu_pair() {
    local env_file="$1" available="$2" name="$3" desired_limit="$4" desired_reservation="$5"
    local limit_key="${name}_CPU_LIMIT"
    local reservation_key="${name}_CPU_RESERVATION"
    local detected_limit current_limit final_limit detected_reservation current_reservation final_reservation

    detected_limit="$(cap_cpu_value "$desired_limit" "$available")"
    current_limit="$(read_env_value "$env_file" "$limit_key")"
    final_limit="$(select_auto_cpu_value "$current_limit" "$detected_limit")"

    detected_reservation="$(cap_cpu_value "$desired_reservation" "$final_limit")"
    current_reservation="$(read_env_value "$env_file" "$reservation_key")"
    final_reservation="$(select_auto_cpu_value "$current_reservation" "$detected_reservation")"
    if LC_ALL=C awk "BEGIN { exit !($final_reservation > $final_limit) }"; then
        final_reservation="$final_limit"
    fi

    if [[ "$current_limit" != "$final_limit" ]]; then
        upsert_env_value "$env_file" "$limit_key" "$final_limit"
        SERVICE_CPU_BUDGET_CHANGED=true
    fi
    if [[ "$current_reservation" != "$final_reservation" ]]; then
        upsert_env_value "$env_file" "$reservation_key" "$final_reservation"
        SERVICE_CPU_BUDGET_CHANGED=true
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

    if LC_ALL=C awk "BEGIN { exit !($final_reservation > $final_limit) }"; then
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

    SERVICE_CPU_BUDGET_CHANGED=false
    ensure_service_cpu_pair "$env_file" "$available" "TTS" "8.0" "2.0"
    ensure_service_cpu_pair "$env_file" "$available" "WHISPER" "4.0" "1.0"
    ensure_service_cpu_pair "$env_file" "$available" "HERMES" "4.0" "0.5"
    ensure_service_cpu_pair "$env_file" "$available" "COMFYUI" "16.0" "2.0"
    if [[ "$SERVICE_CPU_BUDGET_CHANGED" == "true" ]]; then
        ai "Auto-adjusted bundled service CPU budgets (Docker CPUs: ${available})"
    fi
    unset SERVICE_CPU_BUDGET_CHANGED
}

# ── Native llama-server management ──

get_native_llama_status() {
    NATIVE_LLAMA_RUNNING=false
    NATIVE_LLAMA_PID=0
    NATIVE_LLAMA_HEALTHY=false

    local managed_pid
    managed_pid="$(launchctl print "gui/$(id -u)/com.ods.llama-server" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}' || true)"
    if [[ "$managed_pid" =~ ^[0-9]+$ ]] && kill -0 "$managed_pid" 2>/dev/null; then
        printf '%s\n' "$managed_pid" > "$LLAMA_SERVER_PID_FILE"
    fi

    if [[ ! -f "$LLAMA_SERVER_PID_FILE" ]]; then
        return
    fi

    local saved_pid
    saved_pid=$(cat "$LLAMA_SERVER_PID_FILE" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$saved_pid" ]] && return

    if kill -0 "$saved_pid" 2>/dev/null; then
        NATIVE_LLAMA_RUNNING=true
        NATIVE_LLAMA_PID="$saved_pid"

        local native_port
        native_port="$(read_env_value "${INSTALL_DIR}/.env" "ODS_NATIVE_LLAMA_PORT")"
        [[ "$native_port" =~ ^[0-9]+$ ]] || native_port="8080"
        local bind_address probe_host
        bind_address="$(read_env_value "${INSTALL_DIR}/.env" "BIND_ADDRESS")"
        probe_host="$(macos_bind_probe_host "${bind_address:-127.0.0.1}")"
        if curl -sf --max-time 10 "http://${probe_host}:${native_port}/health" >/dev/null 2>&1; then
            NATIVE_LLAMA_HEALTHY=true
        fi
    else
        # Clean up stale PID file
        rm -f "$LLAMA_SERVER_PID_FILE" 2>/dev/null
    fi
}

start_native_llama() {
    local replace="${1:-false}"
    read_ods_env
    if ! macos_configure_llm_bridge_from_env "${INSTALL_DIR}/.env" "$INSTALL_DIR"; then
        ai_err "Could not configure container access to native llama-server"
        return 1
    fi

    get_native_llama_status
    if [[ "${ENV_ODS_MODE:-local}" == "cloud" ]]; then
        $NATIVE_LLAMA_RUNNING && stop_native_llama
        ai "Cloud mode uses LiteLLM; native llama-server remains stopped"
        return 0
    fi
    if $NATIVE_LLAMA_RUNNING && [[ "$replace" != true ]]; then
        if $NATIVE_LLAMA_HEALTHY; then
            ai_ok "Native llama-server already running (PID ${NATIVE_LLAMA_PID})"
        else
            ai "Native llama-server is already running and still loading (PID ${NATIVE_LLAMA_PID})"
        fi
        return
    fi

    local ctx_size="${ENV_CTX_SIZE:-65536}"
    macos_resolve_native_model "$INSTALL_DIR" "$LLAMA_SERVER_BIN" "$ctx_size" || return 1
    local LLAMA_SERVER_BIN="$MACOS_NATIVE_BINARY"
    ctx_size="$MACOS_NATIVE_CONTEXT"
    local gpu_layers="${ENV_N_GPU_LAYERS:-auto}"
    gpu_layers="$(printf '%s' "$gpu_layers" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    gpu_layers="${gpu_layers:-auto}"
    local native_port="${ENV_ODS_NATIVE_LLAMA_PORT:-8080}"
    local bind_address="${ENV_BIND_ADDRESS:-127.0.0.1}"
    local probe_host
    probe_host="$(macos_bind_probe_host "$bind_address")"
    [[ "$native_port" =~ ^[0-9]+$ ]] || native_port="8080"
    local model_path="$MACOS_NATIVE_MODEL_PATH"

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
        --host "$bind_address" --port "$native_port"
        --model "$model_path"
        --ctx-size "$ctx_size"
        --n-gpu-layers "$gpu_layers"
        --reasoning-format "$reasoning_fmt"
        --metrics
    )
    if [[ "$MACOS_NATIVE_PROFILE" == true ]]; then
        llama_args+=("${MACOS_NATIVE_PROFILE_ARGS[@]}")
    else
    llama_args+=(--parallel "${ENV_LLAMA_PARALLEL:-1}")
    [[ -n "${ENV_LLAMA_ARG_FLASH_ATTN:-}" ]] && llama_args+=(--flash-attn "$ENV_LLAMA_ARG_FLASH_ATTN")
    [[ -n "${ENV_LLAMA_ARG_CACHE_TYPE_K:-}" ]] && llama_args+=(--cache-type-k "$ENV_LLAMA_ARG_CACHE_TYPE_K")
    [[ -n "${ENV_LLAMA_ARG_CACHE_TYPE_V:-}" ]] && llama_args+=(--cache-type-v "$ENV_LLAMA_ARG_CACHE_TYPE_V")
    [[ -n "${ENV_LLAMA_ARG_N_CPU_MOE:-}" ]] && llama_args+=(--n-cpu-moe "$ENV_LLAMA_ARG_N_CPU_MOE")
    [[ -n "${ENV_LLAMA_ARG_SPEC_TYPE:-}" ]] && llama_args+=(--spec-type "$ENV_LLAMA_ARG_SPEC_TYPE")
    [[ -n "${ENV_LLAMA_ARG_SPEC_DRAFT_N_MAX:-}" ]] && llama_args+=(--spec-draft-n-max "$ENV_LLAMA_ARG_SPEC_DRAFT_N_MAX")
    [[ -n "${ENV_LLAMA_ARG_SPEC_DRAFT_TYPE_K:-}" ]] && llama_args+=(--spec-draft-type-k "$ENV_LLAMA_ARG_SPEC_DRAFT_TYPE_K")
    [[ -n "${ENV_LLAMA_ARG_SPEC_DRAFT_TYPE_V:-}" ]] && llama_args+=(--spec-draft-type-v "$ENV_LLAMA_ARG_SPEC_DRAFT_TYPE_V")
    macos_resolve_checkpoint_args "$INSTALL_DIR" "$LLAMA_SERVER_BIN" || return 1
    llama_args+=("${MACOS_NATIVE_CHECKPOINT_ARGS[@]}")
    fi

    # Artifact and argument verification must precede termination of working inference.
    [[ "$replace" != true ]] || stop_native_llama
    bash "$INSTALL_DIR/installers/macos/lib/native-llama-service.sh" start \
        "$INSTALL_DIR" "$LLAMA_SERVER_BIN" "$LLAMA_SERVER_PID_FILE" "${llama_args[@]}" || return 1
    local pid
    pid="$(cat "$LLAMA_SERVER_PID_FILE")"

    ai_ok "Native llama-server started (PID ${pid})"
    ai "Waiting for health..."

    local max_wait=60
    local waited=0
    while [[ "$waited" -lt "$max_wait" ]]; do
        sleep 2
        waited=$((waited + 2))
        if curl -sf --max-time 10 "http://${probe_host}:${native_port}/health" >/dev/null 2>&1; then
            ai_ok "Native llama-server healthy"
            return
        fi
    done
    ai_warn "llama-server may still be loading model..."
}

stop_native_llama() {
    get_native_llama_status
    local managed=false
    launchctl print "gui/$(id -u)/com.ods.llama-server" >/dev/null 2>&1 && managed=true
    bash "$INSTALL_DIR/installers/macos/lib/native-llama-service.sh" stop \
        "$INSTALL_DIR" "$LLAMA_SERVER_BIN" "$LLAMA_SERVER_PID_FILE" || return 1
    if $managed; then
        ai_ok "Native llama-server LaunchAgent stopped"
        return 0
    fi
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
    resolve_cli_llm_route

    local flags
    flags=$(get_compose_flags)

    echo ""
    echo -e "  ${GRN}ODS Status (macOS)${NC}"
    echo -e "  ${DGRN}$(printf -- '-%.0s' {1..40})${NC}"

    # Apple Silicon info
    get_apple_silicon_info
    get_system_ram_gb
    echo -e "  ${DGRN}Chip:${NC} ${WHT}${APPLE_CHIP}${NC}"
    echo -e "  ${DGRN}RAM:${NC}  ${WHT}${SYSTEM_RAM_GB} GB (unified memory)${NC}"

    # Active inference backend
    if [[ "$CLI_LLM_MODE" == "cloud" ]]; then
        ai "Inference backend: LiteLLM cloud gateway"
    else
        get_native_llama_status
        if $NATIVE_LLAMA_RUNNING; then
            local health_str="loading"
            $NATIVE_LLAMA_HEALTHY && health_str="healthy"
            ai_ok "llama-server (native Metal): running PID ${NATIVE_LLAMA_PID} (${health_str})"
        else
            ai_warn "llama-server (native Metal): not running"
        fi
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
    local ep_names=("$CLI_LLM_NAME" "Chat UI" "Dashboard" "OpenCode (IDE)")
    local ep_urls=("$CLI_LLM_HEALTH_URL" "http://127.0.0.1:3000" "http://127.0.0.1:3001" "http://127.0.0.1:3003")

    for ((i=0; i<${#ep_names[@]}; i++)); do
        local name="${ep_names[$i]}"
        local url="${ep_urls[$i]}"
        local code
        local -a auth_args=()
        if [[ "$i" -eq 0 ]] && [[ "$CLI_LLM_MODE" == "cloud" ]]; then
            if [[ -z "$CLI_LLM_API_KEY" ]]; then
                ai_warn "${name}: LITELLM_KEY is missing"
                continue
            fi
            auth_args=(-H "Authorization: Bearer ${CLI_LLM_API_KEY}")
        fi
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
            "${auth_args[@]}" "$url" 2>/dev/null || echo "000")
        if [[ "$code" -ge 200 ]] && [[ "$code" -lt 400 ]]; then
            ai_ok "${name}: healthy"
        elif [[ "$i" -ne 0 ]] && { [[ "$code" == "401" ]] || [[ "$code" == "403" ]]; }; then
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
    if [[ -z "$service" ]]; then
        macos_wait_for_bootstrap_compose_safe "start" || return 1
        start_native_llama || return 1
    fi

    local flags
    flags=$(get_compose_flags)

    if [[ "$service" == "ods-proxy" ]]; then
        prepare_proxy_start "$flags" || return 1
    elif [[ -z "$service" || "$service" == "open-webui" ]] && proxy_is_enabled; then
        require_proxy_auth || return 1
    fi

    if [[ "$service" == "llama-server" || "$service" == "llama" ]]; then
        macos_wait_for_bootstrap_compose_safe "start" || return 1
        start_native_llama || return 1
    elif [[ -n "$service" ]]; then
        ai "Starting ${service}..."
        # shellcheck disable=SC2086
        if ! docker compose $flags up -d "$service"; then
            ai_err "Failed to start ${service}."
            return 1
        fi
        ai_ok "${service} started"
    else
        ai "Starting all services..."
        # shellcheck disable=SC2086
        if ! docker compose $flags up -d; then
            ai_err "Failed to start ODS services."
            return 1
        fi
        ai_ok "All services started"
    fi

    if [[ -z "$service" || "$service" == "llama-server" || "$service" == "llama" ]]; then
        macos_maybe_resume_bootstrap_upgrade || true
    fi
}

cmd_stop() {
    local service="${1:-}"
    test_install
    cd "$INSTALL_DIR"

    local flags
    flags=$(get_compose_flags)

    if [[ "$service" == "llama-server" || "$service" == "llama" ]]; then
        stop_native_llama
    elif [[ -n "$service" ]]; then
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

    if [[ "$service" == "ods-proxy" ]]; then
        prepare_proxy_start "$flags" || return 1
    elif [[ -z "$service" || "$service" == "open-webui" ]] && proxy_is_enabled; then
        require_proxy_auth || return 1
    fi

    if [[ "$service" == "llama-server" || "$service" == "llama" ]]; then
        macos_wait_for_bootstrap_compose_safe "restart" || return 1
        start_native_llama true || return 1
    elif [[ -n "$service" ]]; then
        ai "Restarting ${service}..."
        # shellcheck disable=SC2086
        if ! docker compose $flags up -d --force-recreate --no-build --pull never "$service"; then
            ai_err "Failed to restart ${service}."
            return 1
        fi
        ai_ok "${service} restarted"
    else
        # Restart the native llama-server best-effort. A missing model or
        # runtime (a disconnected model drive, a CPU-only install, or a download
        # still in flight) must not abort the whole command: `ods restart` still
        # has to recreate the container services below so the dashboard and UI
        # come back. This mirrors the contract pinned by
        # tests/test-unix-restart-recreate-env.sh; a fatal `|| return 1` here
        # (added in b344d73d) skipped the container restart entirely.
        macos_wait_for_bootstrap_compose_safe "restart" || return 1
        start_native_llama true || ai_warn "Native llama-server did not restart; continuing with the container services."

        ai "Restarting all services..."
        # shellcheck disable=SC2086
        if ! docker compose $flags up -d --force-recreate --no-build --pull never; then
            ai_err "Failed to restart ODS services."
            return 1
        fi
        ai_ok "All services restarted"
    fi

    if [[ -z "$service" || "$service" == "llama-server" || "$service" == "llama" ]]; then
        macos_maybe_resume_bootstrap_upgrade || true
    fi
}

cmd_logs() {
    local service="${1:-}"
    local lines="${2:-100}"

    if [[ -z "$service" ]]; then
        ai "Usage: ./ods-macos.sh logs <service> [lines]"
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
    test_install_dir

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
        # Non KEY=VALUE lines: print verbatim.
        [[ "$line_trimmed" == *=* ]] || { echo -e "  ${WHT}${line_trimmed}${NC}"; continue; }
        # Mask by KEY NAME, not "keyword immediately before =". The old
        # `(SECRET|PASS|TOKEN|KEY)=` regex only matched when the keyword was
        # adjacent to `=`, so OPENCODE_SERVER_PASSWORD, LANGFUSE_DB_PASSWORD,
        # LANGFUSE_SALT, N8N_USER, LANGFUSE_INIT_USER_EMAIL, ... printed their
        # (auto-generated) secret values in cleartext. Match the key name
        # against the same keyword set the Linux CLI's _cmd_config_is_secret
        # falls back to (tr for lowercasing keeps this Bash 3.2 / macOS safe).
        local key key_lc
        key="${line_trimmed%%=*}"
        key_lc=$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]')
        case "$key_lc" in
            *secret*|*password*|*pass*|*token*|*key*|*salt*|*bearer*|*user*|*email*)
                echo -e "  ${DGRN}${key}=***${NC}" ;;
            *)
                echo -e "  ${WHT}${line_trimmed}${NC}" ;;
        esac
    done < "$env_file"
    echo ""
}

cmd_chat() {
    local message="${1:-}"
    if [[ -z "$message" ]]; then
        ai "Usage: ./ods-macos.sh chat \"your message\""
        return
    fi

    resolve_cli_llm_route
    if [[ "$CLI_LLM_MODE" == "cloud" ]] && [[ -z "$CLI_LLM_API_KEY" ]]; then
        ai_err "Chat request cannot authenticate: LITELLM_KEY is missing."
        return 1
    fi

    # Use jq to safely construct JSON payload (prevents injection)
    local payload
    payload=$(jq -n --arg msg "$message" \
        '{model: "default", messages: [{role: "user", content: $msg}], max_tokens: 500}')

    local -a auth_args=()
    if [[ "$CLI_LLM_MODE" == "cloud" ]]; then
        auth_args=(-H "Authorization: Bearer ${CLI_LLM_API_KEY}")
    fi
    local response
    response=$(curl -sf -X POST "${CLI_LLM_BASE_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        "${auth_args[@]}" \
        -d "$payload" 2>/dev/null) || {
        ai_err "Chat request failed."
        ai "Check the active inference backend with: ./ods-macos.sh status"
        return 1
    }

    echo ""
    echo "$response" | jq -r '.choices[0].message.content // .error.message // "Error: no response"'
    echo ""
}

cmd_update_pixel() {
    test_install
    if [[ "${PIXEL_LICENSE_ACCEPTED:-$(read_env_value "$INSTALL_DIR/.env" PIXEL_LICENSE_ACCEPTED)}" != true ]]; then
        ai_err "Pixel update requires PIXEL_LICENSE_ACCEPTED=true after separate written authorization."
        return 1
    fi
    /usr/bin/python3 "${INSTALL_DIR}/installers/macos/lib/pixel-native-update.py" \
        --install-dir "$INSTALL_DIR" --ods-source "$INSTALL_DIR" --license-authorized "$@"
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
    compose_pull_with_retry "$flags"

    ai "Recreating containers..."
    # shellcheck disable=SC2086
    docker compose $flags up -d --force-recreate
    ai_ok "Update complete"
    macos_maybe_resume_bootstrap_upgrade || true

    sleep 5
    cmd_status
}

cmd_version() {
    echo -e "${BGRN}ODS v${ODS_VERSION} (macOS Apple Silicon)${NC}"
}

show_help() {
    echo ""
    echo -e "  ${BGRN}ODS CLI (macOS)${NC}"
    echo -e "  ${DGRN}Version ${ODS_VERSION}${NC}"
    echo ""
    echo -e "  ${WHT}USAGE${NC}"
    echo -e "  ${DGRN}  ./ods-macos.sh <command> [options]${NC}"
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
    echo -e "  ${GRN}  update-pixel${NC}        ${DGRN}Update the native Pixel runtime and services${NC}"
    echo -e "  ${GRN}  version${NC}             ${DGRN}Show version${NC}"
    echo -e "  ${GRN}  help${NC}                ${DGRN}Show this help${NC}"
    echo ""
    echo -e "  ${WHT}EXAMPLES${NC}"
    echo -e "  ${DGRN}  ./ods-macos.sh status${NC}"
    echo -e "  ${DGRN}  ./ods-macos.sh logs llama-server 50${NC}"
    echo -e "  ${DGRN}  ./ods-macos.sh restart open-webui${NC}"
    echo -e "  ${DGRN}  ./ods-macos.sh chat \"What is quantum computing?\"${NC}"
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
                test_install_dir
                ${EDITOR:-nano} "${INSTALL_DIR}/.env"
                ;;
            *)
                cmd_config_show
                ;;
        esac
        ;;
    chat)       cmd_chat "$*" ;;
    update)     cmd_update ;;
    update-pixel) cmd_update_pixel "$@" ;;
    version)    cmd_version ;;
    help)       show_help ;;
    *)
        ai_warn "Unknown command: ${COMMAND}"
        show_help
        ;;
esac
