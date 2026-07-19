#!/bin/bash
# ============================================================================
# bootstrap-upgrade.sh — Background Model Download + Auto Hot-Swap
# ============================================================================
# Runs in the background after the installer starts services with the
# bootstrap model. Downloads the full tier-appropriate model, then swaps
# llama-server to the new model with minimal downtime.
#
# Usage (called by phase 11, not directly by users):
#   nohup bash bootstrap-upgrade.sh \
#       <install_dir> <gguf_file> <gguf_url> <gguf_sha256> \
#       <llm_model> <max_context> [<bootstrap_gguf_file>] \
#       > logs/model-upgrade.log 2>&1 &
#
# Arg 7 (bootstrap_gguf_file) is optional and defaults to the historical
# Qwen3.5-2B-Q4_K_M.gguf for backwards compatibility. Phase 11 must pass the
# canonical $BOOTSTRAP_GGUF_FILE from installers/lib/bootstrap-model.sh so the
# Phase 4b cleanup step removes the actual bootstrap model after hot-swap.
#
# On failure: logs the error, preserves any .part download for resume, and
# exits. The bootstrap model continues running; `ods start`, `ods restart`,
# or re-running the installer retries the full-model upgrade.
# ============================================================================

set -uo pipefail
# Note: no set -e — we handle errors explicitly to avoid killing the
# background process on transient failures.

# ── Arguments ──
INSTALL_DIR="$1"
FULL_GGUF_FILE="$2"
FULL_GGUF_URL="$3"
FULL_GGUF_SHA256="$4"
FULL_LLM_MODEL="$5"
FULL_MAX_CONTEXT="$6"
BOOTSTRAP_GGUF_FILE="${7:-Qwen3.5-2B-Q4_K_M.gguf}"

LOG_TAG="[BOOTSTRAP-UPGRADE]"

log()  { echo "$LOG_TAG $(date '+%H:%M:%S') $*"; }
release_model_lifecycle_lock() {
    if declare -F ods_model_lifecycle_lock_release >/dev/null 2>&1; then
        ods_model_lifecycle_lock_release
    fi
}
prepare_model_lifecycle_lock() {
    [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]] || return 0
    declare -F ods_model_lifecycle_lock_acquire >/dev/null 2>&1 && return 0

    local script_root candidate
    script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 1
    for candidate in \
        "$INSTALL_DIR/installers/lib/model-lifecycle-lock.sh" \
        "$script_root/installers/lib/model-lifecycle-lock.sh"
    do
        if [[ -f "$candidate" ]]; then
            # shellcheck source=installers/lib/model-lifecycle-lock.sh
            . "$candidate"
            return 0
        fi
    done

    log "ERROR: Linux model lifecycle lock helper is missing."
    return 1
}
acquire_model_lifecycle_lock() {
    prepare_model_lifecycle_lock || return 1
    [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]] || return 0
    ods_model_lifecycle_lock_acquire "$INSTALL_DIR" "background full-model activation"
}
fail() { log "ERROR: $*"; release_model_lifecycle_lock; release_upgrade_lock; exit 1; }

if [[ -z "$INSTALL_DIR" || ! -d "$INSTALL_DIR" ]]; then
    log "ERROR: install directory does not exist: ${INSTALL_DIR:-<empty>}"
    exit 1
fi
if ! INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd -P)"; then
    log "ERROR: could not resolve install directory: $INSTALL_DIR"
    exit 1
fi
cd "$INSTALL_DIR" || {
    log "ERROR: could not enter install directory: $INSTALL_DIR"
    exit 1
}

MODELS_DIR="$INSTALL_DIR/data/models"
ENV_FILE="$INSTALL_DIR/.env"
MODELS_INI="$INSTALL_DIR/config/llama-server/models.ini"
STATUS_FILE="$INSTALL_DIR/data/bootstrap-status.json"
UPGRADE_LOCK_DIR=""

# Cross-platform file size (GNU stat on Linux/WSL2, BSD stat on macOS)
# IMPORTANT: Try GNU stat -c %s FIRST (Linux). stat -f on Linux returns filesystem
# block count (not file size). BSD stat -f %z is the macOS fallback.
file_size() {
    if stat -c %s "$1" 2>/dev/null; then
        return
    fi
    stat -f %z "$1" 2>/dev/null || echo 0
}

# Get total size via HTTP HEAD request
get_remote_size() {
    local url="$1"
    curl -sI -L --connect-timeout 10 "$url" 2>/dev/null \
        | grep -i '^content-length:' | tail -1 | tr -dc '0-9'
}

# Write status JSON (atomic via mv)
write_status() {
    local status="$1" percent="${2:-}" downloaded="${3:-0}" total="${4:-0}" speed="${5:-0}" eta="${6:-}"
    local _safe_model="${FULL_GGUF_FILE//\"/\\\"}"
    cat > "$STATUS_FILE.tmp" << STATUSEOF
{
  "status": "$status",
  "model": "$_safe_model",
  "percent": ${percent:-null},
  "bytesDownloaded": $downloaded,
  "bytesTotal": $total,
  "speedBytesPerSec": $speed,
  "eta": "${eta:-}",
  "updatedAt": "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date '+%Y-%m-%dT%H:%M:%SZ')"
}
STATUSEOF
    mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}

status_percent() {
    local downloaded="${1:-0}" total="${2:-0}"
    if [[ "$total" -le 0 ]]; then
        echo ""
        return
    fi
    local display_bytes="$downloaded"
    [[ "$display_bytes" -lt 0 ]] && display_bytes=0
    [[ "$display_bytes" -gt "$total" ]] && display_bytes="$total"
    LC_ALL=C awk "BEGIN { printf \"%.1f\", ($display_bytes / $total) * 100 }"
}

write_failed_download_status() {
    local part_file="${1:-}" total="${2:-0}" message="${3:-}"
    local downloaded=0 percent=""
    if [[ -n "$part_file" && -f "$part_file" ]]; then
        downloaded=$(file_size "$part_file")
    fi
    percent=$(status_percent "$downloaded" "$total")
    write_status "failed" "$percent" "$downloaded" "$total" 0 "$message"
}

compose_recreate_llama_server_with_retry() {
    local -a compose_args=("$@")
    local max_attempts="${ODS_BOOTSTRAP_COMPOSE_RETRY_ATTEMPTS:-3}"
    if ! [[ "$max_attempts" =~ ^[0-9]+$ ]] || (( max_attempts < 1 )); then
        max_attempts=3
    fi

    local attempt=1
    local retries=$(( max_attempts - 1 ))
    local output rc
    while true; do
        output=$(env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE \
            $DOCKER_COMPOSE_CMD "${compose_args[@]}" up -d --force-recreate --no-deps llama-server 2>&1)
        rc=$?
        [[ -n "$output" ]] && printf '%s\n' "$output"
        (( rc == 0 )) && return 0

        if (( attempt >= max_attempts )) || \
           ! grep -Eiq 'dependency failed to start|No such container|container .* (exited|is unhealthy)|service .* failed to start|failed to start.*container' <<<"$output"; then
            return "$rc"
        fi

        log "Docker reported a transient llama-server recreate failure; waiting briefly and retrying (${attempt}/${retries})."
        sleep "${ODS_BOOTSTRAP_COMPOSE_RETRY_DELAY:-15}"
        attempt=$(( attempt + 1 ))
    done
}

release_upgrade_lock() {
    if [[ -n "${UPGRADE_LOCK_DIR:-}" && -d "$UPGRADE_LOCK_DIR" ]]; then
        rm -rf "$UPGRADE_LOCK_DIR"
    fi
    UPGRADE_LOCK_DIR=""
}

acquire_upgrade_lock() {
    local tmp_root="${TMPDIR:-/tmp}"
    local lock_key
    lock_key="$(printf '%s\0%s' "$INSTALL_DIR" "$FULL_GGUF_FILE" | cksum | awk '{print $1}')"
    local lock_dir="$tmp_root/ods-bootstrap-upgrade-${lock_key}.lock"
    local pid_file="$lock_dir/pid"
    local existing_pid=""

    mkdir -p "$(dirname "$lock_dir")"
    while ! mkdir "$lock_dir" 2>/dev/null; do
        existing_pid=""
        if [[ -f "$pid_file" ]]; then
            existing_pid="$(tr -dc '0-9' < "$pid_file" 2>/dev/null || true)"
        fi

        if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
            log "Another bootstrap model upgrade is already running (pid $existing_pid); leaving it in control."
            if [[ ! -f "$STATUS_FILE" ]]; then
                write_status "downloading" "" 0 0 0 "Another bootstrap model upgrade is already running."
            fi
            exit 0
        fi

        log "Removing stale bootstrap model upgrade lock: $lock_dir"
        rm -rf "$lock_dir"
    done

    UPGRADE_LOCK_DIR="$lock_dir"
    printf '%s\n' "$$" > "$pid_file"
    trap 'release_model_lifecycle_lock; release_upgrade_lock' EXIT
}

model_sha256() {
    local path="$1"
    if command -v sha256sum &>/dev/null; then
        sha256sum "$path" 2>/dev/null | awk '{print $1}'
    elif command -v shasum &>/dev/null; then
        shasum -a 256 "$path" 2>/dev/null | awk '{print $1}'
    else
        return 2
    fi
}

verify_model_integrity() {
    local path="$1" actual_hash
    [[ -n "$FULL_GGUF_SHA256" ]] || return 0

    actual_hash="$(model_sha256 "$path")"
    case "$?" in
        0) ;;
        2)
            log "WARNING: No checksum tool available — skipping SHA256 verification"
            return 0
            ;;
        *)
            log "Could not compute SHA256 for $path"
            return 1
            ;;
    esac

    if [[ -z "$actual_hash" ]]; then
        log "Could not compute SHA256 for $path"
        return 1
    fi
    if [[ "$actual_hash" != "$FULL_GGUF_SHA256" ]]; then
        log "SHA256 mismatch (expected: $FULL_GGUF_SHA256, got: $actual_hash)."
        return 1
    fi

    log "SHA256 verified"
    return 0
}

ACTIVE_CONFIG_SNAPSHOT_DIR=""

snapshot_file_state() {
    local source_path="$1" snapshot_path="$2"
    mkdir -p "$(dirname "$snapshot_path")" || return 1
    if [[ -f "$source_path" ]]; then
        cp -p "$source_path" "$snapshot_path"
    else
        : > "${snapshot_path}.missing"
    fi
}

restore_file_state() {
    local snapshot_path="$1" target_path="$2"
    if [[ -f "$snapshot_path" ]]; then
        mkdir -p "$(dirname "$target_path")" || return 1
        cp -p "$snapshot_path" "$target_path"
    elif [[ -f "${snapshot_path}.missing" ]]; then
        rm -f "$target_path"
    fi
}

snapshot_active_model_config() {
    local snapshot_base include_windows_lemonade="${1:-false}"
    snapshot_base="${INSTALL_DIR}/data"
    mkdir -p "$snapshot_base" 2>/dev/null || return 1
    ACTIVE_CONFIG_SNAPSHOT_DIR="$(mktemp -d "${snapshot_base}/bootstrap-upgrade-active-config.XXXXXX" 2>/dev/null || true)"
    [[ -n "$ACTIVE_CONFIG_SNAPSHOT_DIR" ]] || return 1

    if [[ -f "$ENV_FILE" ]]; then
        cp -p "$ENV_FILE" "$ACTIVE_CONFIG_SNAPSHOT_DIR/env" || return 1
    else
        : > "$ACTIVE_CONFIG_SNAPSHOT_DIR/env.missing"
    fi

    if [[ -f "$MODELS_INI" ]]; then
        mkdir -p "$ACTIVE_CONFIG_SNAPSHOT_DIR/config-llama-server" || return 1
        cp -p "$MODELS_INI" "$ACTIVE_CONFIG_SNAPSHOT_DIR/config-llama-server/models.ini" || return 1
    else
        : > "$ACTIVE_CONFIG_SNAPSHOT_DIR/models.ini.missing"
    fi

    if [[ "$include_windows_lemonade" == "true" ]]; then
        snapshot_file_state \
            "$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template" \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/hermes-template" || return 1
        snapshot_file_state \
            "$INSTALL_DIR/data/hermes/config.yaml" \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/hermes-live" || return 1
        snapshot_file_state \
            "$INSTALL_DIR/config/litellm/lemonade.yaml" \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/litellm-lemonade" || return 1
        : > "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade.included"
    fi
}

restore_active_model_config() {
    [[ -n "${ACTIVE_CONFIG_SNAPSHOT_DIR:-}" && -d "$ACTIVE_CONFIG_SNAPSHOT_DIR" ]] || return 1

    if [[ -f "$ACTIVE_CONFIG_SNAPSHOT_DIR/env" ]]; then
        cp -p "$ACTIVE_CONFIG_SNAPSHOT_DIR/env" "$ENV_FILE" || return 1
    elif [[ -f "$ACTIVE_CONFIG_SNAPSHOT_DIR/env.missing" ]]; then
        rm -f "$ENV_FILE"
    fi

    if [[ -f "$ACTIVE_CONFIG_SNAPSHOT_DIR/config-llama-server/models.ini" ]]; then
        mkdir -p "$(dirname "$MODELS_INI")" || return 1
        cp -p "$ACTIVE_CONFIG_SNAPSHOT_DIR/config-llama-server/models.ini" "$MODELS_INI" || return 1
    elif [[ -f "$ACTIVE_CONFIG_SNAPSHOT_DIR/models.ini.missing" ]]; then
        rm -f "$MODELS_INI"
    fi

    if [[ -f "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade.included" ]]; then
        restore_file_state \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/hermes-template" \
            "$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template" || return 1
        restore_file_state \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/hermes-live" \
            "$INSTALL_DIR/data/hermes/config.yaml" || return 1
        restore_file_state \
            "$ACTIVE_CONFIG_SNAPSHOT_DIR/windows-lemonade/litellm-lemonade" \
            "$INSTALL_DIR/config/litellm/lemonade.yaml" || return 1
    fi

    rm -rf "$ACTIVE_CONFIG_SNAPSHOT_DIR"
    ACTIVE_CONFIG_SNAPSHOT_DIR=""
}

discard_active_model_config_snapshot() {
    if [[ -n "${ACTIVE_CONFIG_SNAPSHOT_DIR:-}" ]]; then
        rm -rf "$ACTIVE_CONFIG_SNAPSHOT_DIR"
        ACTIVE_CONFIG_SNAPSHOT_DIR=""
    fi
}

restore_docker_llama_server_after_swap_failure() {
    local health_url="${1:-}"
    local compose_arg_count=0
    local rollback_healthy=false

    log "Restoring previous active model config after Docker llama-server swap failure..."
    if ! restore_active_model_config; then
        log "WARNING: could not restore previous active model config; inspect $ENV_FILE and $MODELS_INI"
        return 1
    fi

    if declare -p COMPOSE_ARGS >/dev/null 2>&1; then
        compose_arg_count=${#COMPOSE_ARGS[@]}
    fi
    if [[ "$compose_arg_count" -eq 0 || -z "${DOCKER_COMPOSE_CMD:-}" ]]; then
        log "WARNING: cannot recreate llama-server with previous config because compose flags are unavailable."
        return 1
    fi

    log "Recreating llama-server with previous active model config..."
    if ! compose_recreate_llama_server_with_retry "${COMPOSE_ARGS[@]}"; then
        log "WARNING: rollback recreate failed; inspect docker logs ods-llama-server"
        return 1
    fi

    [[ -n "$health_url" ]] || return 0
    log "Waiting for restored llama-server health at $health_url ..."
    for _rollback_i in $(seq 1 60); do
        if curl -sf --max-time 5 "$health_url" >/dev/null 2>&1; then
            rollback_healthy=true
            break
        fi
        sleep 5
    done

    if [[ "$rollback_healthy" == "true" ]]; then
        log "Rollback complete: llama-server is healthy with the previous active model config."
        return 0
    fi

    log "WARNING: rollback config was restored, but llama-server did not become healthy within the wait window."
    return 1
}

docker_llama_server_container_failed_after_swap() {
    [[ -n "${DOCKER_CMD:-}" ]] || return 1

    local state
    state=$($DOCKER_CMD inspect ods-llama-server \
        --format '{{.State.Status}} {{.State.Restarting}} {{.State.ExitCode}}' \
        2>/dev/null || true)
    case "$state" in
        restarting*|exited*|dead*)
            return 0
            ;;
        running\ true\ *|created\ true\ *)
            return 0
            ;;
    esac

    return 1
}

BOOTSTRAP_SWAP_BACKUP_PATH=""

move_bootstrap_model_aside_for_windows_swap() {
    [[ -f "$BOOTSTRAP_PATH" ]] || return 0

    BOOTSTRAP_SWAP_BACKUP_PATH="${BOOTSTRAP_PATH}.ods-swap-backup"
    rm -f "$BOOTSTRAP_SWAP_BACKUP_PATH"
    mv "$BOOTSTRAP_PATH" "$BOOTSTRAP_SWAP_BACKUP_PATH" || return 1
    log "Moved bootstrap model aside before Windows Lemonade full-model restart: $(basename "$BOOTSTRAP_PATH")"
}

restore_bootstrap_model_after_windows_swap_failure() {
    [[ -n "$BOOTSTRAP_SWAP_BACKUP_PATH" && -f "$BOOTSTRAP_SWAP_BACKUP_PATH" ]] || return 0

    mv "$BOOTSTRAP_SWAP_BACKUP_PATH" "$BOOTSTRAP_PATH" || return 1
    BOOTSTRAP_SWAP_BACKUP_PATH=""
    log "Restored bootstrap model after Windows Lemonade swap failure: $(basename "$BOOTSTRAP_PATH")"
}

discard_bootstrap_model_backup_after_windows_swap() {
    [[ -n "$BOOTSTRAP_SWAP_BACKUP_PATH" && -f "$BOOTSTRAP_SWAP_BACKUP_PATH" ]] || return 0

    rm -f "$BOOTSTRAP_SWAP_BACKUP_PATH"
    BOOTSTRAP_SWAP_BACKUP_PATH=""
    log "Removed bootstrap model after verified Windows Lemonade full-model serving: $(basename "$BOOTSTRAP_PATH")"
}

sync_windows_opencode_config() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) ;;
        *) return 0 ;;
    esac

    local sync_script="$INSTALL_DIR/scripts/update-windows-opencode-config.ps1"
    [[ -f "$sync_script" ]] || return 0

    local ps_cmd=""
    if command -v powershell.exe >/dev/null 2>&1; then
        ps_cmd="powershell.exe"
    elif command -v pwsh.exe >/dev/null 2>&1; then
        ps_cmd="pwsh.exe"
    fi
    [[ -n "$ps_cmd" ]] || return 0

    local install_dir_arg="$INSTALL_DIR"
    local sync_script_arg="$sync_script"
    if command -v cygpath >/dev/null 2>&1; then
        install_dir_arg=$(cygpath -w "$INSTALL_DIR")
        sync_script_arg=$(cygpath -w "$sync_script")
    fi

    log "Refreshing Windows OpenCode config for model: $FULL_GGUF_FILE"
    "$ps_cmd" -NoProfile -ExecutionPolicy Bypass -File "$sync_script_arg" -InstallDir "$install_dir_arg" \
        >/dev/null 2>&1 || log "WARNING: OpenCode config refresh failed (non-fatal)"
}

read_env_value() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 0
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"\047\r'
}

write_env_value() {
    local key="$1" value="$2" tmp
    [[ -f "$ENV_FILE" ]] || return 1
    tmp="${ENV_FILE}.tmp.$$"
    if ! awk -v k="$key" -v v="$value" '
        BEGIN { found = 0 }
        index($0, k "=") == 1 { print k "=" v; found = 1; next }
        { print }
        END { if (!found) print k "=" v }
    ' "$ENV_FILE" > "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
}

is_windows_bash() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
        *) return 1 ;;
    esac
}

windows_path() {
    local path="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$path"
    else
        printf '%s\n' "$path"
    fi
}

windows_ps_command() {
    if command -v powershell.exe >/dev/null 2>&1; then
        printf '%s\n' "powershell.exe"
    elif command -v pwsh.exe >/dev/null 2>&1; then
        printf '%s\n' "pwsh.exe"
    fi
}

restart_windows_lemonade_with_full_model() {
    is_windows_bash || return 1

    local target_gguf="${1:-$FULL_GGUF_FILE}" target_label="${2:-full model}"
    [[ -n "$target_gguf" ]] || return 1

    local runtime llm_backend managed runtime_mode lemonade_external
    runtime="$(read_env_value AMD_INFERENCE_RUNTIME | tr '[:upper:]' '[:lower:]')"
    llm_backend="$(read_env_value LLM_BACKEND | tr '[:upper:]' '[:lower:]')"
    [[ "$runtime" == "lemonade" || "$llm_backend" == "lemonade" ]] || return 1
    managed="$(read_env_value AMD_INFERENCE_MANAGED | tr '[:upper:]' '[:lower:]')"
    runtime_mode="$(read_env_value AMD_INFERENCE_RUNTIME_MODE | tr '[:upper:]' '[:lower:]')"
    lemonade_external="$(read_env_value LEMONADE_EXTERNAL | tr '[:upper:]' '[:lower:]')"
    if [[ "$managed" == "false" || "$runtime_mode" == "external-lemonade" || "$lemonade_external" == "true" ]]; then
        log "Skipping native Windows Lemonade restart because the runtime is externally managed."
        return 1
    fi

    local ps_cmd
    ps_cmd="$(windows_ps_command)"
    [[ -n "$ps_cmd" ]] || {
        log "WARNING: no PowerShell executable found; cannot restart native Windows Lemonade."
        return 1
    }

    local pid_file bind_addr lemonade_port helper_path env_path
    pid_file="$INSTALL_DIR/data/llama-server.pid"
    bind_addr="$(read_env_value BIND_ADDRESS)"
    [[ -n "$bind_addr" ]] || bind_addr="127.0.0.1"
    lemonade_port="$(read_env_value AMD_INFERENCE_PORT)"
    [[ -n "$lemonade_port" ]] || lemonade_port="8080"
    helper_path="$INSTALL_DIR/installers/windows/lib/backend-contract.ps1"
    env_path="$ENV_FILE"
    if [[ ! -f "$helper_path" ]]; then
        log "WARNING: Windows Lemonade launch helper is missing: $helper_path"
        return 1
    fi

    log "Restarting native Windows Lemonade with ${target_label}: ${target_gguf}"
    local ps_output model_id
    if ! ps_output=$(
        ODS_WIN_PID_FILE="$(windows_path "$pid_file")" \
        ODS_WIN_MODELS_DIR="$(windows_path "$MODELS_DIR")" \
        ODS_WIN_BIND_ADDR="$bind_addr" \
        ODS_WIN_LEMONADE_PORT="$lemonade_port" \
        ODS_WIN_LEMONADE_HELPER="$(windows_path "$helper_path")" \
        ODS_WIN_ENV_PATH="$(windows_path "$env_path")" \
        ODS_WIN_LEMONADE_DIAGNOSTIC_LOG="$(windows_path "$INSTALL_DIR/logs/lemonade-launch.log")" \
        ODS_WIN_LEMONADE_TASK="ODSLemonadeRuntime" \
        ODS_WIN_GGUF_FILE="$target_gguf" \
        "$ps_cmd" -NoProfile -ExecutionPolicy Bypass -Command '
        $ErrorActionPreference = "Stop"

        $helperPath = $env:ODS_WIN_LEMONADE_HELPER
        if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
            throw "Windows Lemonade launch helper not found: $helperPath"
        }
        . $helperPath
        $exe = Resolve-ODSLemonadeExe
        if (-not $exe) { throw "lemonade-server.exe not found under Program Files roots" }
        $port = [int]$env:ODS_WIN_LEMONADE_PORT
        $adminApiKey = Get-ODSLemonadeAdminApiKey -EnvPath $env:ODS_WIN_ENV_PATH
        $launchContract = Get-ODSLemonadeLaunchContract `
            -ExecutablePath $exe -Port $port -BindAddress $env:ODS_WIN_BIND_ADDR `
            -ModelsDir $env:ODS_WIN_MODELS_DIR -AdminApiKey $adminApiKey
        $taskName = $env:ODS_WIN_LEMONADE_TASK
        try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch { }

        $binDir = Split-Path -Parent $exe
        $userProfile = [Environment]::GetFolderPath("UserProfile")
        $lemonadeCacheBin = if ($userProfile) { Join-Path (Join-Path (Join-Path $userProfile ".cache") "lemonade") "bin" } else { $null }
        $odsModelsDir = $env:ODS_WIN_MODELS_DIR

        function Test-ODSLemonadeProcess {
            param($ProcessInfo)
            if (-not $ProcessInfo) { return $false }
            return (
                ($ProcessInfo.ExecutablePath -and $ProcessInfo.ExecutablePath.StartsWith($binDir, [StringComparison]::OrdinalIgnoreCase)) -or
                ($lemonadeCacheBin -and $ProcessInfo.ExecutablePath -and $ProcessInfo.ExecutablePath.StartsWith($lemonadeCacheBin, [StringComparison]::OrdinalIgnoreCase)) -or
                ($odsModelsDir -and $ProcessInfo.CommandLine -and
                    $ProcessInfo.CommandLine.IndexOf($odsModelsDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                    $ProcessInfo.CommandLine.IndexOf("lemonade", [StringComparison]::OrdinalIgnoreCase) -ge 0)
            )
        }

        function Stop-ODSProcessId {
            param([int]$ProcessId)
            $processInfo = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction SilentlyContinue
            if (-not $processInfo) { return }
            if (-not (Test-ODSLemonadeProcess $processInfo)) {
                throw "Refusing to stop unowned process $ProcessId on configured Lemonade port $port"
            }
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 30; $i++) {
                $old = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
                if (-not $old) { return }
                Start-Sleep -Milliseconds 500
            }
        }

        $pidPath = $env:ODS_WIN_PID_FILE
        if (Test-Path $pidPath) {
            $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
            if ($rawPid -match "^\d+$") {
                Stop-ODSProcessId -ProcessId ([int]$rawPid)
            }
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        }

        # Lemonade also has a process-wide router singleton. If the saved PID
        # is stale or points at a child that has already exited, Start-Process
        # can report success while the new router immediately exits with
        # "Another instance of lemonade-router is already running"; the swap
        # then polls the old bootstrap instance forever. Clear the configured
        # listener and any Lemonade Server child processes before launching the
        # full-model instance.
        $deadline = (Get-Date).AddSeconds(20)
        do {
            $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
            foreach ($listener in $listeners) {
                if ($listener.OwningProcess -gt 0) {
                    Stop-ODSProcessId -ProcessId ([int]$listener.OwningProcess)
                }
            }
            if ($listeners.Count -eq 0) { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)

        $lemonadeChildren = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            Test-ODSLemonadeProcess $_
        })
        foreach ($child in $lemonadeChildren) {
            Stop-ODSProcessId -ProcessId ([int]$child.ProcessId)
        }

        $stillListening = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($stillListening.Count -gt 0) {
            throw "port $port is still occupied after stopping the previous Lemonade instance"
        }

        $logPath = Join-Path $env:TEMP "lemonade-server.log"
        Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue

        $action = New-ODSLemonadeScheduledTaskAction `
            -Contract $launchContract -EnvPath $env:ODS_WIN_ENV_PATH `
            -DiagnosticLogPath $env:ODS_WIN_LEMONADE_DIAGNOSTIC_LOG
        $launchMethod = "scheduled task"
        $proc = $null
        try {
            $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddYears(1))
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero)
            $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
                -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } catch {
            $launchMethod = "direct process"
            Write-Warning "Could not start Lemonade through Task Scheduler: $_"
            $proc = Start-ODSLemonadeDirectProcess -Contract $launchContract
        }

        $healthy = $false
        for ($i = 0; $i -lt 45; $i++) {
            try {
                $health = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/v1/health" -f $port) -TimeoutSec 3 -UseBasicParsing
                if ([int]$health.StatusCode -ge 200 -and [int]$health.StatusCode -lt 300) {
                    $healthy = $true
                    break
                }
            } catch { }
            Start-Sleep -Seconds 1
        }
        if (-not $healthy -and $launchMethod -eq "scheduled task") {
            $scheduledDiagnostics = Get-ODSLemonadeLaunchDiagnostics -TaskName $taskName
            Write-Warning "Lemonade scheduled task did not start a healthy router. $(Format-ODSLemonadeLaunchDiagnostics -Diagnostics $scheduledDiagnostics)"
            try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch { }
            $launchMethod = "direct process"
            $proc = Start-ODSLemonadeDirectProcess -Contract $launchContract
            for ($i = 0; $i -lt 15; $i++) {
                try {
                    $health = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/v1/health" -f $port) -TimeoutSec 3 -UseBasicParsing
                    if ([int]$health.StatusCode -ge 200 -and [int]$health.StatusCode -lt 300) {
                        $healthy = $true
                        break
                    }
                } catch { }
                Start-Sleep -Seconds 1
            }
        }
        if (-not $healthy) {
            $diagnostics = Get-ODSLemonadeLaunchDiagnostics -TaskName $taskName -ChildProcess $proc
            throw "Lemonade $launchMethod did not become healthy after restart. $(Format-ODSLemonadeLaunchDiagnostics -Diagnostics $diagnostics)"
        }
        $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) | Select-Object -First 1
        if (-not $listener -or $listener.OwningProcess -le 0) {
            throw "Lemonade health passed, but no listener process was found on port $port"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $pidPath) -Force | Out-Null
        Set-Content -LiteralPath $pidPath -Value $listener.OwningProcess
        if ($launchContract.RequiresRuntimeConfiguration) {
            $null = Set-ODSLemonadeModernRuntimeConfig `
                -Port $port -ModelsDir $env:ODS_WIN_MODELS_DIR -AdminApiKey $adminApiKey
        }
        $modelId = Resolve-ODSLemonadeModelId `
            -Port $port -GgufFile $env:ODS_WIN_GGUF_FILE `
            -VersionOverride ([string]$launchContract.Version)
        Write-Output ("__ODS_LEMONADE_MODEL_ID__={0}" -f $modelId)
    ' 2>&1
    ); then
        log "WARNING: native Windows Lemonade restart failed: $(printf '%s' "$ps_output" | tr '\r\n' ' ' | tail -c 800)"
        return 1
    fi

    model_id="$(printf '%s\n' "$ps_output" | sed -n 's/^__ODS_LEMONADE_MODEL_ID__=//p' | tail -1 | tr -d '\r')"
    if [[ -z "$model_id" ]]; then
        log "WARNING: native Windows Lemonade restart did not report a model ID."
        return 1
    fi
    log "Waiting for native Windows Lemonade to serve ${model_id} ..."
    local _swap_attempts
    # A freshly-swapped full model can take several minutes to register in
    # --extra-models-dir and then load on the first completion. The old 12x10s
    # (~2 min) budget timed out before a 22 GB MoE (Qwen3.6-35B-A3B) was even
    # listed, so the swap reverted to bootstrap (#1517). Use the same longer
    # budget as the llama.cpp warm-up path; override with ODS_LEMONADE_SWAP_ATTEMPTS.
    _swap_attempts="${ODS_LEMONADE_SWAP_ATTEMPTS:-60}"
    case "$_swap_attempts" in ''|*[!0-9]*|0) _swap_attempts=60 ;; esac
    for _i in $(seq 1 "$_swap_attempts"); do
        if curl -sf --max-time 5 "http://127.0.0.1:${lemonade_port}/api/v1/models" 2>/dev/null \
            | grep -q "\"id\"[[:space:]]*:[[:space:]]*\"${model_id}\""; then
            if curl -sf --max-time 240 -X POST \
                "http://127.0.0.1:${lemonade_port}/api/v1/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{\"model\":\"${model_id}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}" \
                >/dev/null 2>&1; then
                if ! write_env_value LEMONADE_MODEL "$model_id"; then
                    log "WARNING: ${model_id} completed, but ODS could not persist LEMONADE_MODEL."
                    return 1
                fi
                log "SUCCESS: native Windows Lemonade completed with ${model_id}"
                return 0
            fi
            log "Windows Lemonade lists ${model_id}, but completion is not ready yet (attempt $_i/${_swap_attempts})."
        else
            log "Waiting for Windows Lemonade to register ${model_id} (attempt $_i/${_swap_attempts})."
        fi
        sleep 10
    done

    log "WARNING: native Windows Lemonade did not complete with ${model_id} after ${_swap_attempts} attempts."
    return 1
}

restart_windows_lemonade_with_previous_model() {
    local previous_gguf="${1:-}"
    restart_windows_lemonade_with_full_model "$previous_gguf" "previous model"
}

restart_windows_native_llama_server_with_full_model() {
    is_windows_bash || return 1

    local runtime managed runtime_mode location ps_cmd
    runtime="$(read_env_value AMD_INFERENCE_RUNTIME | tr '[:upper:]' '[:lower:]')"
    managed="$(read_env_value AMD_INFERENCE_MANAGED | tr '[:upper:]' '[:lower:]')"
    runtime_mode="$(read_env_value AMD_INFERENCE_RUNTIME_MODE | tr '[:upper:]' '[:lower:]')"
    location="$(read_env_value AMD_INFERENCE_LOCATION | tr '[:upper:]' '[:lower:]')"
    [[ "$runtime_mode" == "windows-llama-server-fallback" || ( "$runtime" == "llama-server" && "$location" == "host" ) ]] || return 1
    [[ "$managed" == "false" || "$location" == "external" ]] && return 1

    ps_cmd="$(windows_ps_command)"
    [[ -n "$ps_cmd" ]] || {
        log "WARNING: no PowerShell executable found; cannot restart native Windows llama-server."
        return 1
    }

    local pid_file llama_exe model_path rollback_model_path log_path bind_addr ctx_size llama_port reasoning reasoning_fmt
    pid_file="$INSTALL_DIR/data/llama-server.pid"
    llama_exe="$INSTALL_DIR/llama-server/llama-server.exe"
    model_path="$MODELS_DIR/$FULL_GGUF_FILE"
    rollback_model_path="$MODELS_DIR/$BOOTSTRAP_GGUF_FILE"
    log_path="$INSTALL_DIR/data/llama-server.log"
    bind_addr="$(read_env_value BIND_ADDRESS)"
    [[ -n "$bind_addr" ]] || bind_addr="127.0.0.1"
    ctx_size="$(read_env_value CTX_SIZE)"
    [[ -n "$ctx_size" ]] || ctx_size="$(read_env_value MAX_CONTEXT)"
    [[ -n "$ctx_size" ]] || ctx_size="$FULL_MAX_CONTEXT"
    llama_port="$(read_env_value AMD_INFERENCE_PORT)"
    [[ -n "$llama_port" ]] || llama_port="8080"
    reasoning="$(read_env_value LLAMA_REASONING)"
    [[ -n "$reasoning" ]] || reasoning="off"
    case "$reasoning" in
        off) reasoning_fmt="none" ;;
        on)  reasoning_fmt="deepseek" ;;
        *)   reasoning_fmt="$reasoning" ;;
    esac

    [[ -f "$llama_exe" ]] || {
        log "WARNING: llama-server.exe not found at $llama_exe. Cannot hot-swap native Windows llama-server."
        return 1
    }
    [[ -f "$model_path" ]] || {
        log "WARNING: full model not found at $model_path. Cannot hot-swap native Windows llama-server."
        return 1
    }

    log "Restarting native Windows llama-server with full model..."
    ODS_WIN_PID_FILE="$(windows_path "$pid_file")" \
    ODS_WIN_LLAMA_EXE="$(windows_path "$llama_exe")" \
    ODS_WIN_MODEL_PATH="$(windows_path "$model_path")" \
    ODS_WIN_ROLLBACK_MODEL_PATH="$(windows_path "$rollback_model_path")" \
    ODS_WIN_LOG_PATH="$(windows_path "$log_path")" \
    ODS_WIN_BIND_ADDR="$bind_addr" \
    ODS_WIN_LLAMA_PORT="$llama_port" \
    ODS_WIN_CTX_SIZE="$ctx_size" \
    ODS_WIN_REASONING_FORMAT="$reasoning_fmt" \
    ODS_WIN_FLASH_ATTN="$(read_env_value LLAMA_ARG_FLASH_ATTN)" \
    ODS_WIN_CACHE_TYPE_K="$(read_env_value LLAMA_ARG_CACHE_TYPE_K)" \
    ODS_WIN_CACHE_TYPE_V="$(read_env_value LLAMA_ARG_CACHE_TYPE_V)" \
    ODS_WIN_N_CPU_MOE="$(read_env_value LLAMA_ARG_N_CPU_MOE)" \
    ODS_WIN_PARALLEL="$(read_env_value LLAMA_PARALLEL)" \
    ODS_WIN_CHECKPOINT_EVERY_N_TOKENS="$(read_env_value LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS)" \
    ODS_WIN_NO_CACHE_PROMPT="$(read_env_value LLAMA_ARG_NO_CACHE_PROMPT)" \
    ODS_WIN_SPEC_TYPE="$(read_env_value LLAMA_ARG_SPEC_TYPE)" \
    ODS_WIN_SPEC_DRAFT_N_MAX="$(read_env_value LLAMA_ARG_SPEC_DRAFT_N_MAX)" \
    "$ps_cmd" -NoProfile -ExecutionPolicy Bypass -Command '
        $ErrorActionPreference = "Stop"

        function Stop-ODSProcessId {
            param([int]$ProcessId)
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 30; $i++) {
                $old = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
                if (-not $old) { return }
                Start-Sleep -Milliseconds 500
            }
        }

        function Stop-ODSLlamaListeners {
            param([int]$Port)
            $deadline = (Get-Date).AddSeconds(20)
            do {
                $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
                foreach ($listener in $listeners) {
                    if ($listener.OwningProcess -gt 0) {
                        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$listener.OwningProcess) -ErrorAction SilentlyContinue
                        if ($proc -and (
                            ($proc.Name -like "llama-server*") -or
                            ($proc.ExecutablePath -and $proc.ExecutablePath.Equals($env:ODS_WIN_LLAMA_EXE, [StringComparison]::OrdinalIgnoreCase)) -or
                            ($proc.CommandLine -and $proc.CommandLine.IndexOf("llama-server", [StringComparison]::OrdinalIgnoreCase) -ge 0)
                        )) {
                            Stop-ODSProcessId -ProcessId ([int]$listener.OwningProcess)
                        }
                    }
                }
                if ($listeners.Count -eq 0) { return }
                Start-Sleep -Milliseconds 500
            } while ((Get-Date) -lt $deadline)
        }

        function Start-ODSLlama {
            param([string]$ModelPath)

            $args = @(
                "--model", $ModelPath,
                "--host", $env:ODS_WIN_BIND_ADDR,
                "--port", $env:ODS_WIN_LLAMA_PORT,
                "--n-gpu-layers", "999",
                "--ctx-size", $env:ODS_WIN_CTX_SIZE,
                "--reasoning-format", $env:ODS_WIN_REASONING_FORMAT,
                "--metrics"
            )
            if ($env:ODS_WIN_FLASH_ATTN) { $args += @("--flash-attn", $env:ODS_WIN_FLASH_ATTN) }
            if ($env:ODS_WIN_CACHE_TYPE_K) { $args += @("--cache-type-k", $env:ODS_WIN_CACHE_TYPE_K) }
            if ($env:ODS_WIN_CACHE_TYPE_V) { $args += @("--cache-type-v", $env:ODS_WIN_CACHE_TYPE_V) }
            if ($env:ODS_WIN_N_CPU_MOE) { $args += @("--n-cpu-moe", $env:ODS_WIN_N_CPU_MOE) }
            if ($env:ODS_WIN_PARALLEL) { $args += @("--parallel", $env:ODS_WIN_PARALLEL) }
            if ($env:ODS_WIN_CHECKPOINT_EVERY_N_TOKENS) { $args += @("--checkpoint-every-n-tokens", $env:ODS_WIN_CHECKPOINT_EVERY_N_TOKENS) }
            if ($env:ODS_WIN_NO_CACHE_PROMPT -and $env:ODS_WIN_NO_CACHE_PROMPT -notin @("0", "false", "off", "no")) { $args += @("--no-cache-prompt") }
            if ($env:ODS_WIN_SPEC_TYPE) { $args += @("--spec-type", $env:ODS_WIN_SPEC_TYPE) }
            if ($env:ODS_WIN_SPEC_DRAFT_N_MAX) { $args += @("--spec-draft-n-max", $env:ODS_WIN_SPEC_DRAFT_N_MAX) }

            New-Item -ItemType Directory -Path (Split-Path -Parent $env:ODS_WIN_PID_FILE) -Force | Out-Null
            New-Item -ItemType Directory -Path (Split-Path -Parent $env:ODS_WIN_LOG_PATH) -Force | Out-Null
            $proc = Start-Process -FilePath $env:ODS_WIN_LLAMA_EXE `
                -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $env:ODS_WIN_LOG_PATH -RedirectStandardError ($env:ODS_WIN_LOG_PATH + ".err") -PassThru
            Set-Content -LiteralPath $env:ODS_WIN_PID_FILE -Value $proc.Id
            return $proc
        }

        function Wait-ODSLlamaHealth {
            param([int]$ProcessId, [int]$Port)
            for ($i = 0; $i -lt 60; $i++) {
                $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
                if (-not $proc) { return $false }
                try {
                    $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 5 -UseBasicParsing
                    if ([int]$resp.StatusCode -eq 200) { return $true }
                } catch { }
                Start-Sleep -Seconds 5
            }
            return $false
        }

        $pidPath = $env:ODS_WIN_PID_FILE
        if (Test-Path $pidPath) {
            $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
            if ($rawPid -match "^\d+$") {
                Stop-ODSProcessId -ProcessId ([int]$rawPid)
            }
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        }

        $port = [int]$env:ODS_WIN_LLAMA_PORT
        Stop-ODSLlamaListeners -Port $port

        $fullProc = Start-ODSLlama -ModelPath $env:ODS_WIN_MODEL_PATH
        if (Wait-ODSLlamaHealth -ProcessId ([int]$fullProc.Id) -Port $port) { exit 0 }

        Stop-ODSProcessId -ProcessId ([int]$fullProc.Id)
        if ($env:ODS_WIN_ROLLBACK_MODEL_PATH -and (Test-Path $env:ODS_WIN_ROLLBACK_MODEL_PATH)) {
            $rollbackProc = Start-ODSLlama -ModelPath $env:ODS_WIN_ROLLBACK_MODEL_PATH
            [void](Wait-ODSLlamaHealth -ProcessId ([int]$rollbackProc.Id) -Port $port)
        }
        exit 1
    ' >/dev/null 2>&1 || {
        log "WARNING: native Windows llama-server restart failed."
        return 1
    }

    log "SUCCESS: native Windows llama-server running with ${FULL_GGUF_FILE}"
    return 0
}

patch_hermes_yaml_with_sed() {
    local path="$1" model="$2" context_length="$3" base_url="${4:-}" request_timeout_seconds="${5:-180}"
    [[ -f "$path" ]] || return 1

    local model_sed base_url_sed
    model_sed="$(printf '%s' "$model" | sed 's/[\\&|]/\\&/g')"
    base_url_sed="$(printf '%s' "$base_url" | sed 's/[\\&|]/\\&/g')"

    local sed_args=(
        -e "s|^  default: \".*\"[[:space:]]*$|  default: \"${model_sed}\"|"
        -e "s|^  context_length: .*|  context_length: ${context_length}|"
        -e "s|^    context_length: .*|    context_length: ${context_length}|"
    )
    if [[ "$request_timeout_seconds" =~ ^[0-9]+$ && "$request_timeout_seconds" != "180" ]]; then
        sed_args+=(-e "s|^    request_timeout_seconds: 180[[:space:]]*$|    request_timeout_seconds: ${request_timeout_seconds}|")
    fi
    if [[ -n "$base_url" ]]; then
        sed_args+=(-e "s|^  base_url: \".*\"[[:space:]]*$|  base_url: \"${base_url_sed}\"|")
    fi

    if sed -i.bak \
        "${sed_args[@]}" \
        "$path" 2>&1; then
        rm -f "${path}.bak"
    else
        [[ -f "${path}.bak" ]] && mv "${path}.bak" "$path"
        return 1
    fi

    grep -Fq "  default: \"${model}\"" "$path" \
        && grep -Fq "  context_length: ${context_length}" "$path" \
        && { [[ -z "$base_url" ]] || grep -Fq "  base_url: \"${base_url}\"" "$path"; }
}

patch_hermes_model_after_swap() {
    local runtime llm_backend hermes_base_url old_model new_model tpl live live_host_patch_failed hermes_request_timeout
    runtime="$(read_env_value AMD_INFERENCE_RUNTIME | tr '[:upper:]' '[:lower:]')"
    llm_backend="$(read_env_value LLM_BACKEND | tr '[:upper:]' '[:lower:]')"
    hermes_base_url="$(read_env_value HERMES_LLM_BASE_URL)"
    old_model="$BOOTSTRAP_GGUF_FILE"
    new_model="$FULL_GGUF_FILE"
    if [[ "$runtime" == "lemonade" || "$llm_backend" == "lemonade" ]]; then
        old_model="extra.$BOOTSTRAP_GGUF_FILE"
        new_model="$(read_env_value LEMONADE_MODEL)"
        [[ -n "$new_model" ]] || new_model="extra.$FULL_GGUF_FILE"
    fi

    log "Patching Hermes config after full-model swap: ${old_model} -> ${new_model}"
    hermes_request_timeout=180
    if is_windows_bash || [[ "$runtime" == "lemonade" || "$llm_backend" == "lemonade" ]]; then
        hermes_request_timeout=900
    fi

    tpl="$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template"
    if [[ -f "$tpl" ]]; then
        if ! patch_hermes_yaml_with_sed "$tpl" "$new_model" "$FULL_MAX_CONTEXT" "$hermes_base_url" "$hermes_request_timeout"; then
            log "ERROR: Could not patch ${tpl} after full-model swap."
            return 1
        fi
    fi

    live="$INSTALL_DIR/data/hermes/config.yaml"
    live_host_patch_failed=false
    if [[ -f "$live" ]]; then
        if ! patch_hermes_yaml_with_sed "$live" "$new_model" "$FULL_MAX_CONTEXT" "$hermes_base_url" "$hermes_request_timeout"; then
            live_host_patch_failed=true
            log "WARNING: Could not patch ${live} after full-model swap; will try the container copy if Hermes is running."
        fi
    fi

    if [[ -n "$DOCKER_CMD" ]] && $DOCKER_CMD ps --filter name=ods-hermes --format '{{.Names}}' 2>/dev/null | grep -q ods-hermes; then
        local live_patch new_model_sed hermes_base_url_sed
        new_model_sed="$(printf '%s' "$new_model" | sed 's/[\\&|]/\\&/g')"
        hermes_base_url_sed="$(printf '%s' "$hermes_base_url" | sed 's/[\\&|]/\\&/g')"
        live_patch="sed -i -e 's|^  default: \".*\"[[:space:]]*$|  default: \"${new_model_sed}\"|' -e 's|^  context_length: .*|  context_length: ${FULL_MAX_CONTEXT}|' -e 's|^    context_length: .*|    context_length: ${FULL_MAX_CONTEXT}|'"
        if [[ -n "$hermes_base_url" ]]; then
            live_patch="${live_patch} -e 's|^  base_url: \".*\"|  base_url: \"${hermes_base_url_sed}\"|'"
        fi
        if [[ "$hermes_request_timeout" != "180" ]]; then
            live_patch="${live_patch} -e 's|^    request_timeout_seconds: 180[[:space:]]*$|    request_timeout_seconds: ${hermes_request_timeout}|'"
        fi
        live_patch="${live_patch} /opt/data/config.yaml"
        $DOCKER_CMD exec ods-hermes sh -c \
            "$live_patch" 2>&1 || {
                log "ERROR: Could not patch Hermes live config after full-model swap."
                return 1
            }
        $DOCKER_CMD restart ods-hermes 2>&1 || {
            log "ERROR: Could not restart Hermes after full-model swap."
            return 1
        }
    elif [[ "$live_host_patch_failed" == "true" ]]; then
        log "ERROR: Could not patch Hermes live config after full-model swap."
        return 1
    fi

    return 0
}

WINDOWS_LEMONADE_LITELLM_PRESENT=false
WINDOWS_LEMONADE_HERMES_PRESENT=false
WINDOWS_LEMONADE_OPENCLAW_PRESENT=false
WINDOWS_LEMONADE_COMPOSE_ARGS=()
WINDOWS_LEMONADE_SWAP_FAILURE=""
WINDOWS_LEMONADE_ROLLBACK_VERIFIED=false

windows_lemonade_container_present() {
    local container_name="$1"
    [[ -n "${DOCKER_CMD:-}" ]] || return 1
    $DOCKER_CMD ps -a --filter "name=${container_name}" --format '{{.Names}}' 2>/dev/null \
        | grep -Fxq "$container_name"
}

capture_windows_lemonade_dependent_state() {
    WINDOWS_LEMONADE_LITELLM_PRESENT=false
    WINDOWS_LEMONADE_HERMES_PRESENT=false
    WINDOWS_LEMONADE_OPENCLAW_PRESENT=false
    windows_lemonade_container_present ods-litellm && WINDOWS_LEMONADE_LITELLM_PRESENT=true
    windows_lemonade_container_present ods-hermes && WINDOWS_LEMONADE_HERMES_PRESENT=true
    windows_lemonade_container_present ods-openclaw && WINDOWS_LEMONADE_OPENCLAW_PRESENT=true
}

load_windows_lemonade_compose_args() {
    [[ ${#WINDOWS_LEMONADE_COMPOSE_ARGS[@]} -eq 0 ]] || return 0
    [[ -n "${DOCKER_COMPOSE_CMD:-}" ]] || return 1

    if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
        read -ra WINDOWS_LEMONADE_COMPOSE_ARGS <<< "$(cat "$INSTALL_DIR/.compose-flags")"
    elif [[ -x "$INSTALL_DIR/scripts/resolve-compose-stack.sh" ]]; then
        local tier resolved_env resolved_flags
        tier="$(read_env_value TIER)"
        [[ -n "$tier" ]] || tier="1"
        resolved_env=$("$INSTALL_DIR/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "$tier" \
            --gpu-backend amd \
            --env 2>/dev/null || true)
        resolved_flags=$(printf '%s\n' "$resolved_env" | sed -n 's/^COMPOSE_FLAGS="\([^"]*\)".*/\1/p')
        [[ -n "$resolved_flags" ]] && read -ra WINDOWS_LEMONADE_COMPOSE_ARGS <<< "$resolved_flags"
    fi

    [[ ${#WINDOWS_LEMONADE_COMPOSE_ARGS[@]} -gt 0 ]]
}

refresh_windows_lemonade_litellm_after_swap() {
    local model_id="${1:-}"
    [[ -n "$model_id" ]] || return 1

    local litellm_dir litellm_config lemonade_port lemonade_api_base lemonade_api_key
    local renderer_script renderer_py renderer_ok=false
    litellm_dir="$INSTALL_DIR/config/litellm"
    litellm_config="$litellm_dir/lemonade.yaml"
    lemonade_port="$(read_env_value AMD_INFERENCE_PORT)"
    [[ -n "$lemonade_port" ]] || lemonade_port="8080"
    lemonade_api_base="http://host.docker.internal:${lemonade_port}/api/v1"
    lemonade_api_key="$(read_env_value LITELLM_LEMONADE_API_KEY)"
    [[ -n "$lemonade_api_key" ]] || lemonade_api_key="sk-lemonade"

    log "Regenerating LiteLLM config for resolved Lemonade model: ${model_id}"
    mkdir -p "$litellm_dir" || return 1
    renderer_script="$INSTALL_DIR/scripts/render-runtime-configs.py"
    renderer_py="${ODS_PYTHON_CMD:-}"
    if [[ -z "$renderer_py" && -f "$INSTALL_DIR/lib/python-cmd.sh" ]]; then
        . "$INSTALL_DIR/lib/python-cmd.sh"
        renderer_py="$(ods_detect_python_cmd 2>/dev/null || true)"
    fi
    if [[ -n "$renderer_py" && -f "$renderer_script" ]]; then
        if "$renderer_py" "$renderer_script" \
            --surface litellm-lemonade \
            --ods-mode lemonade \
            --gpu-backend amd \
            --gguf-file "$FULL_GGUF_FILE" \
            --lemonade-model-id "$model_id" \
            --lemonade-api-base "$lemonade_api_base" \
            --litellm-key "$lemonade_api_key" \
            --output-root "$INSTALL_DIR" \
            --write >/dev/null 2>&1; then
            renderer_ok=true
        fi
    fi

    if [[ "$renderer_ok" != "true" ]]; then
        local litellm_tmp="${litellm_config}.tmp.$$"
        if ! cat > "$litellm_tmp" << LITELLM_WINDOWS_LEMONADE_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/${model_id}
      api_base: ${lemonade_api_base}
      api_key: ${lemonade_api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/${model_id}
      api_base: ${lemonade_api_base}
      api_key: ${lemonade_api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
LITELLM_WINDOWS_LEMONADE_EOF
        then
            rm -f "$litellm_tmp"
            return 1
        fi
        mv "$litellm_tmp" "$litellm_config" || {
            rm -f "$litellm_tmp"
            return 1
        }
    fi

    grep -Fq "model: openai/${model_id}" "$litellm_config" || return 1
    grep -Fq "api_base: ${lemonade_api_base}" "$litellm_config" || return 1

    if [[ "$WINDOWS_LEMONADE_LITELLM_PRESENT" == "true" ]]; then
        log "Restarting LiteLLM with the resolved Lemonade route..."
        $DOCKER_CMD restart ods-litellm 2>&1 || return 1
    fi
}

recreate_windows_lemonade_openclaw() {
    [[ "$WINDOWS_LEMONADE_OPENCLAW_PRESENT" == "true" ]] || return 0
    if ! load_windows_lemonade_compose_args; then
        log "ERROR: cannot recreate OpenClaw because the active compose stack is unavailable."
        return 1
    fi

    log "Recreating OpenClaw with the resolved Lemonade model..."
    env -u GGUF_FILE -u LLM_MODEL -u LEMONADE_MODEL -u MAX_CONTEXT -u CTX_SIZE \
        $DOCKER_COMPOSE_CMD "${WINDOWS_LEMONADE_COMPOSE_ARGS[@]}" \
        up -d --force-recreate --no-deps openclaw 2>&1
}

openclaw_env_value_from_inspect() {
    local key="$1" env_output="$2"
    printf '%s\n' "$env_output" | sed -n "s/^${key}=//p" | tail -1 | tr -d '\r'
}

verify_windows_lemonade_openclaw_model_env() {
    local expected_model="${1:-}" env_output lemonade_model gguf_file llm_model ollama_url actual_model
    [[ "$WINDOWS_LEMONADE_OPENCLAW_PRESENT" == "true" ]] || return 0
    [[ -n "$expected_model" ]] || return 1
    [[ -n "${DOCKER_CMD:-}" ]] || return 1

    if ! env_output=$($DOCKER_CMD inspect --type container --format '{{range .Config.Env}}{{println .}}{{end}}' ods-openclaw 2>/dev/null); then
        log "ERROR: could not inspect OpenClaw environment after recreate."
        return 1
    fi

    lemonade_model="$(openclaw_env_value_from_inspect LEMONADE_MODEL "$env_output")"
    gguf_file="$(openclaw_env_value_from_inspect GGUF_FILE "$env_output")"
    llm_model="$(openclaw_env_value_from_inspect LLM_MODEL "$env_output")"
    ollama_url="$(openclaw_env_value_from_inspect OLLAMA_URL "$env_output")"
    if [[ -n "$lemonade_model" ]]; then
        actual_model="$lemonade_model"
    elif [[ "$ollama_url" == */api || "$ollama_url" == */api/ ]]; then
        [[ -n "$gguf_file" ]] && actual_model="extra.${gguf_file}"
    else
        actual_model="$llm_model"
    fi

    if [[ "$actual_model" == "$expected_model" ]]; then
        log "Verified OpenClaw recreated with model ${actual_model}."
        return 0
    fi

    log "ERROR: OpenClaw recreated with model ${actual_model:-<empty>}; expected ${expected_model}."
    return 1
}

verify_windows_lemonade_downstream_route() {
    local model_id="${1:-}" route_label="${2:-model}"
    [[ -n "$model_id" ]] || return 1

    local route_base route_key route_url route_mode="host" route_container=""
    local attempts request_timeout request_body escaped_model
    escaped_model="${model_id//\\/\\\\}"
    escaped_model="${escaped_model//\"/\\\"}"
    request_body="{\"model\":\"${escaped_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"route check\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}"

    if [[ "$WINDOWS_LEMONADE_HERMES_PRESENT" == "true" ]]; then
        route_base="$(read_env_value HERMES_LLM_BASE_URL)"
        [[ -n "$route_base" ]] || route_base="http://litellm:4000/v1"
        route_key="$(read_env_value HERMES_LLM_API_KEY)"
        [[ -n "$route_key" ]] || route_key="$(read_env_value LITELLM_KEY)"
        route_mode="container"
        route_container="ods-hermes"
    elif [[ "$WINDOWS_LEMONADE_LITELLM_PRESENT" == "true" ]]; then
        local litellm_port
        litellm_port="$(read_env_value LITELLM_PORT)"
        [[ -n "$litellm_port" ]] || litellm_port="4000"
        route_base="http://127.0.0.1:${litellm_port}/v1"
        route_key="$(read_env_value LITELLM_KEY)"
    else
        local lemonade_port
        lemonade_port="$(read_env_value AMD_INFERENCE_PORT)"
        [[ -n "$lemonade_port" ]] || lemonade_port="8080"
        route_base="http://127.0.0.1:${lemonade_port}/api/v1"
        route_key="$(read_env_value LITELLM_LEMONADE_API_KEY)"
    fi
    route_url="${route_base%/}/chat/completions"

    attempts="${ODS_LEMONADE_DOWNSTREAM_ATTEMPTS:-30}"
    case "$attempts" in ''|*[!0-9]*|0) attempts=30 ;; esac
    request_timeout="${ODS_LEMONADE_DOWNSTREAM_TIMEOUT:-120}"
    case "$request_timeout" in ''|*[!0-9]*|0) request_timeout=120 ;; esac

    log "Verifying ${route_label} through the configured downstream route: ${route_base}"
    for _route_i in $(seq 1 "$attempts"); do
        if [[ "$route_mode" == "container" ]]; then
            if $DOCKER_CMD exec "$route_container" curl -sf --max-time "$request_timeout" -X POST \
                "$route_url" \
                -H "Content-Type: application/json" \
                -H "Authorization: Bearer ${route_key}" \
                -d "$request_body" >/dev/null 2>&1; then
                log "Verified ${route_label} through ${route_base} with model ${model_id}."
                return 0
            fi
        elif curl -sf --max-time "$request_timeout" -X POST \
            "$route_url" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${route_key}" \
            -d "$request_body" >/dev/null 2>&1; then
            log "Verified ${route_label} through ${route_base} with model ${model_id}."
            return 0
        fi
        sleep 2
    done

    log "ERROR: ${route_label} did not complete through ${route_base} with model ${model_id}."
    return 1
}

restart_windows_lemonade_dependents_after_rollback() {
    local dependents_ok=true
    if [[ "$WINDOWS_LEMONADE_LITELLM_PRESENT" == "true" ]]; then
        log "Restarting LiteLLM with its restored config..."
        $DOCKER_CMD restart ods-litellm 2>&1 || dependents_ok=false
    fi
    if [[ "$WINDOWS_LEMONADE_HERMES_PRESENT" == "true" ]]; then
        log "Restarting Hermes with its restored config..."
        $DOCKER_CMD restart ods-hermes 2>&1 || dependents_ok=false
    fi
    if [[ "$WINDOWS_LEMONADE_OPENCLAW_PRESENT" == "true" ]]; then
        log "Recreating OpenClaw with the restored model environment..."
        recreate_windows_lemonade_openclaw || dependents_ok=false
    fi
    [[ "$dependents_ok" == "true" ]]
}

snapshot_env_value() {
    local key="$1" snapshot_env="${ACTIVE_CONFIG_SNAPSHOT_DIR:-}/env"
    [[ -f "$snapshot_env" ]] || return 0
    grep -E "^${key}=" "$snapshot_env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"\047\r'
}

rollback_windows_lemonade_swap() {
    local previous_gguf previous_model_id rollback_ok=true inference_restored=false route_verified=false
    previous_gguf="$(snapshot_env_value GGUF_FILE)"
    previous_model_id="$(snapshot_env_value LEMONADE_MODEL)"
    [[ -n "$previous_gguf" ]] || previous_gguf="$BOOTSTRAP_GGUF_FILE"

    log "Rolling back the Windows Lemonade model transaction..."
    restore_bootstrap_model_after_windows_swap_failure || rollback_ok=false
    restore_active_model_config || rollback_ok=false

    if restart_windows_lemonade_with_previous_model "$previous_gguf"; then
        inference_restored=true
        previous_model_id="$(read_env_value LEMONADE_MODEL)"
    else
        rollback_ok=false
    fi

    restart_windows_lemonade_dependents_after_rollback || rollback_ok=false
    if [[ "$WINDOWS_LEMONADE_OPENCLAW_PRESENT" == "true" && -n "$previous_model_id" ]]; then
        verify_windows_lemonade_openclaw_model_env "$previous_model_id" || rollback_ok=false
    fi
    if [[ "$inference_restored" == "true" && -n "$previous_model_id" ]] \
        && verify_windows_lemonade_downstream_route "$previous_model_id" "previous model route"; then
        route_verified=true
    else
        rollback_ok=false
    fi

    if [[ "$rollback_ok" == "true" && "$route_verified" == "true" ]]; then
        log "Rollback verified: the previous model completed through the restored downstream route."
        return 0
    fi

    log "WARNING: rollback was attempted, but the previous downstream model route could not be fully restored and verified."
    return 1
}

windows_lemonade_swap_failed() {
    WINDOWS_LEMONADE_SWAP_FAILURE="$1"
    WINDOWS_LEMONADE_ROLLBACK_VERIFIED=false
    log "Windows Lemonade full-model activation failed: ${WINDOWS_LEMONADE_SWAP_FAILURE}"
    log "Restoring previous active model config after Windows Lemonade swap timeout or post-swap failure..."
    if rollback_windows_lemonade_swap; then
        WINDOWS_LEMONADE_ROLLBACK_VERIFIED=true
    fi
    return 1
}

activate_windows_lemonade_full_model() {
    local model_id
    if ! restart_windows_lemonade_with_full_model; then
        windows_lemonade_swap_failed "native Lemonade did not load it after swap (registration timeout)"
        return 1
    fi
    model_id="$(read_env_value LEMONADE_MODEL)"
    if [[ -z "$model_id" ]]; then
        windows_lemonade_swap_failed "native Lemonade did not persist its resolved full-model ID"
        return 1
    fi
    if ! refresh_windows_lemonade_litellm_after_swap "$model_id"; then
        windows_lemonade_swap_failed "LiteLLM config regeneration or reload failed"
        return 1
    fi
    if ! patch_hermes_model_after_swap; then
        windows_lemonade_swap_failed "Hermes config update or restart failed"
        return 1
    fi
    if ! recreate_windows_lemonade_openclaw; then
        windows_lemonade_swap_failed "OpenClaw recreate failed"
        return 1
    fi
    if ! verify_windows_lemonade_openclaw_model_env "$model_id"; then
        windows_lemonade_swap_failed "OpenClaw recreated with a stale model environment"
        return 1
    fi
    if ! verify_windows_lemonade_downstream_route "$model_id" "full model route"; then
        windows_lemonade_swap_failed "the full model failed through the configured downstream route"
        return 1
    fi
    return 0
}

refresh_windows_native_litellm_local_config_after_swap() {
    is_windows_bash || return 0

    local runtime runtime_mode location managed
    runtime="$(read_env_value AMD_INFERENCE_RUNTIME | tr '[:upper:]' '[:lower:]')"
    runtime_mode="$(read_env_value AMD_INFERENCE_RUNTIME_MODE | tr '[:upper:]' '[:lower:]')"
    location="$(read_env_value AMD_INFERENCE_LOCATION | tr '[:upper:]' '[:lower:]')"
    managed="$(read_env_value AMD_INFERENCE_MANAGED | tr '[:upper:]' '[:lower:]')"

    [[ "$managed" != "false" && "$location" == "host" ]] || return 0
    [[ "$runtime_mode" == "windows-llama-server-fallback" || "$runtime" == "llama-server" ]] || return 0

    local litellm_dir litellm_config native_port native_api_base model_sed
    litellm_dir="$INSTALL_DIR/config/litellm"
    litellm_config="$litellm_dir/local.yaml"
    native_port="$(read_env_value AMD_INFERENCE_PORT)"
    [[ -n "$native_port" ]] || native_port="8080"
    native_api_base="http://host.docker.internal:${native_port}/v1"
    model_sed="${FULL_GGUF_FILE//\"/\\\"}"

    log "Updating LiteLLM local config for native Windows llama-server: ${FULL_GGUF_FILE}"
    mkdir -p "$litellm_dir" || return 1
    cat > "$litellm_config" << LITELLM_NATIVE_LOCAL_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/${model_sed}
      api_base: ${native_api_base}
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/*
      api_base: ${native_api_base}
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
LITELLM_NATIVE_LOCAL_EOF

    if [[ -n "$DOCKER_CMD" ]] && $DOCKER_CMD ps --filter name=ods-litellm --format '{{.Names}}' 2>/dev/null | grep -q ods-litellm; then
        $DOCKER_CMD restart ods-litellm 2>&1 || log "WARNING: LiteLLM restart failed after native Windows config refresh (non-fatal)"
    fi
}

refresh_lemonade_after_bootstrap_cleanup() {
    local gpu_backend llm_backend
    gpu_backend="$(read_env_value GPU_BACKEND | tr '[:upper:]' '[:lower:]')"
    llm_backend="$(read_env_value LLM_BACKEND | tr '[:upper:]' '[:lower:]')"

    [[ "$gpu_backend" == "amd" || "$llm_backend" == "lemonade" ]] || return 0
    is_windows_bash && return 0
    [[ "$FULL_GGUF_FILE" != "$BOOTSTRAP_GGUF" ]] || return 0
    [[ -n "$DOCKER_CMD" ]] || return 1

    local compose_args=()
    if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
        read -ra compose_args <<< "$(cat "$INSTALL_DIR/.compose-flags")"
    fi
    if [[ ${#compose_args[@]} -eq 0 || -z "$DOCKER_COMPOSE_CMD" ]]; then
        log "WARNING: cannot refresh Lemonade after bootstrap cleanup because compose flags are unavailable."
        return 1
    fi

    log "Refreshing Lemonade after bootstrap model cleanup so stale model metadata is dropped..."
    env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE \
        $DOCKER_COMPOSE_CMD "${compose_args[@]}" up -d --force-recreate --no-deps llama-server 2>&1 || return 1

    local lemonade_port model_id old_model_id models_json
    lemonade_port="$(read_env_value OLLAMA_PORT)"
    [[ -n "$lemonade_port" ]] || lemonade_port="8080"
    model_id="$(read_env_value LEMONADE_MODEL)"
    [[ -n "$model_id" ]] || model_id="extra.${FULL_GGUF_FILE//\"/\\\"}"
    old_model_id="extra.${BOOTSTRAP_GGUF//\"/\\\"}"

    for _i in $(seq 1 60); do
        models_json="$(curl -sf --max-time 5 "http://127.0.0.1:${lemonade_port}/api/v1/models" 2>/dev/null || true)"
        if echo "$models_json" | grep -q "\"id\"[[:space:]]*:[[:space:]]*\"${model_id}\"" \
            && ! echo "$models_json" | grep -q "\"id\"[[:space:]]*:[[:space:]]*\"${old_model_id}\""; then
            if curl -sf --max-time 240 -X POST \
                "http://127.0.0.1:${lemonade_port}/api/v1/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{\"model\":\"${model_id}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}" \
                >/dev/null 2>&1; then
                log "Lemonade refreshed with only the full model advertised: ${model_id}"
                return 0
            fi
            log "Lemonade lists ${model_id} after cleanup, but completion is not ready yet (attempt $_i/60)."
        else
            log "Waiting for Lemonade to drop bootstrap metadata after cleanup (attempt $_i/60)."
        fi
        sleep 5
    done

    return 1
}

# Background monitor: polls .part file size every 2s
monitor_download() {
    local part_file="$1" total_bytes="$2"

    # Wait for curl to create the .part file (up to 30s)
    for _wait in $(seq 1 30); do
        [[ -f "$part_file" ]] && break
        sleep 1
    done
    [[ -f "$part_file" ]] || return 0

    local prev_bytes=0 prev_time
    prev_time=$(date +%s)

    while [[ -f "$part_file" ]]; do
        sleep 2
        [[ -f "$part_file" ]] || break

        local current_bytes
        current_bytes=$(file_size "$part_file")
        local now
        now=$(date +%s)
        local elapsed=$((now - prev_time))

        local speed=0
        if [[ $elapsed -gt 0 && $current_bytes -ge $prev_bytes ]]; then
            speed=$(( (current_bytes - prev_bytes) / elapsed ))
        fi

        local percent="null"
        local eta=""
        local progress_bytes="$current_bytes"
        if [[ $total_bytes -gt 0 ]]; then
            [[ "$progress_bytes" -gt "$total_bytes" ]] && progress_bytes="$total_bytes"
            percent=$(status_percent "$progress_bytes" "$total_bytes")
            if [[ $speed -gt 0 ]]; then
                local remaining=$(( total_bytes - progress_bytes ))
                local eta_secs=$(( remaining / speed ))
                local eta_min=$(( eta_secs / 60 ))
                local eta_sec=$(( eta_secs % 60 ))
                eta="${eta_min}m ${eta_sec}s"
            else
                eta="calculating..."
            fi
        fi

        write_status "downloading" "$percent" "$progress_bytes" "$total_bytes" "$speed" "$eta"
        prev_bytes=$current_bytes
        prev_time=$now
    done
}

# ── Docker permission detection ──
# This script runs detached via nohup, so DOCKER_CMD from the parent installer
# is not inherited. For Linux installs we MUST be able to talk to the docker
# daemon — silently failing here leaves the user running the small bootstrap
# model forever. macOS installs use a native llama-server PID file and never
# enter the docker hot-swap path; skip detection there. Mirrors the
# sudo-fallback pattern in installers/phases/05-docker.sh.
DOCKER_CMD=""
DOCKER_COMPOSE_CMD=""
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_CMD="docker"
    elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
        DOCKER_CMD="sudo docker"
        log "Detected docker requires sudo (user not in docker group). Using 'sudo docker'."
    elif [[ ! -f "$INSTALL_DIR/data/.llama-server.pid" ]]; then
        # Linux install: docker is the only hot-swap path. Failing silently
        # would leave the bootstrap model running forever — fail loudly.
        log "ERROR: docker is installed but not accessible by this user."
        log "       Tried 'docker info' and 'sudo -n docker info' — both failed."
        log "       The bootstrap model will continue running. Fix one of:"
        log "         1. Re-login (so 'docker' group membership takes effect), then re-run this script."
        log "         2. Configure passwordless sudo for 'docker' (e.g. NOPASSWD in /etc/sudoers.d)."
        write_status "failed"
        exit 1
    fi

    if [[ -n "$DOCKER_CMD" ]]; then
        # Pick docker compose v2 (plugin) if available, else legacy docker-compose v1.
        if $DOCKER_CMD compose version >/dev/null 2>&1; then
            DOCKER_COMPOSE_CMD="$DOCKER_CMD compose"
        elif command -v docker-compose >/dev/null 2>&1; then
            if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
                DOCKER_COMPOSE_CMD="sudo docker-compose"
            else
                DOCKER_COMPOSE_CMD="docker-compose"
            fi
        fi
    fi
fi

log "Starting full model download: $FULL_GGUF_FILE"
log "URL: $FULL_GGUF_URL"
log "Target: $MODELS_DIR/$FULL_GGUF_FILE"

# ── Phase 1: Download the full model ──
mkdir -p "$MODELS_DIR"
acquire_upgrade_lock

# Get total file size for progress calculation
TOTAL_BYTES=$(get_remote_size "$FULL_GGUF_URL")
[[ -z "$TOTAL_BYTES" ]] && TOTAL_BYTES=0
log "Expected file size: $TOTAL_BYTES bytes"

# Write initial status
write_status "starting" "" 0 "$TOTAL_BYTES" 0 "calculating..."

_part_path="$MODELS_DIR/$FULL_GGUF_FILE.part"
_final_path="$MODELS_DIR/$FULL_GGUF_FILE"
_dl_success=false
_download_attempts="${ODS_BOOTSTRAP_DOWNLOAD_ATTEMPTS:-6}"
case "$_download_attempts" in
    ''|*[!0-9]*|0) _download_attempts=6 ;;
esac

if [[ -f "$_final_path" ]]; then
    acquire_model_lifecycle_lock || fail "Could not serialize full-model finalization."
    log "Full model already exists on disk; verifying before reuse"
    if verify_model_integrity "$_final_path"; then
        _dl_success=true
        write_status "verifying" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
    else
        log "Existing full model failed integrity; deleting and retrying from a clean file."
        rm -f "$_final_path"
        release_model_lifecycle_lock
    fi
fi

if [[ -f "$_part_path" && "$TOTAL_BYTES" -gt 0 ]]; then
    _part_bytes="$(file_size "$_part_path")"
    if [[ "$_part_bytes" -gt "$TOTAL_BYTES" ]]; then
        log "Existing partial is larger than the remote file (got $_part_bytes, expected $TOTAL_BYTES); deleting corrupt resume state."
        rm -f "$_part_path"
    fi
fi

if [[ "$_dl_success" != "true" ]]; then
    monitor_download "$_part_path" "$TOTAL_BYTES" &
    _monitor_pid=$!
    trap 'kill $_monitor_pid 2>/dev/null || true; write_failed_download_status "$_part_path" "$TOTAL_BYTES" "Download interrupted; partial file preserved for resume."; release_model_lifecycle_lock; release_upgrade_lock; exit 1' TERM INT

    # Download with resume support. curl success is not enough: finalizing the
    # .part file can fail, and checksum verification can expose a corrupt
    # resume. Keep retries inside the script so the detached upgrade can
    # recover without leaving the user on the bootstrap model.
    for ((_attempt=1; _attempt<=_download_attempts; _attempt++)); do
        [[ $_attempt -gt 1 ]] && log "Retry attempt $_attempt of $_download_attempts..." && sleep 5

        if [[ -f "$_part_path" && "$TOTAL_BYTES" -gt 0 ]]; then
            _part_bytes="$(file_size "$_part_path")"
            if [[ "$_part_bytes" -gt "$TOTAL_BYTES" ]]; then
                log "Partial file grew larger than expected before attempt $_attempt (got $_part_bytes, expected $TOTAL_BYTES); deleting corrupt resume state."
                rm -f "$_part_path"
            elif [[ "$_part_bytes" -eq "$TOTAL_BYTES" ]]; then
                log "Partial file already has the expected size; promoting it for integrity verification."
                acquire_model_lifecycle_lock || fail "Could not serialize full-model finalization."
                if ! mv "$_part_path" "$_final_path"; then
                    log "Download attempt $_attempt failed while finalizing $_part_path -> $_final_path"
                    release_model_lifecycle_lock
                fi
            fi
        fi

        if [[ ! -f "$_final_path" ]]; then
            # Let this script own retry/resume. curl's internal retry path can
            # restart the transfer from byte zero after a long connection reset,
            # truncating an otherwise good multi-GB .part file.
            if curl -fSL -C - --connect-timeout 30 --speed-time 300 --speed-limit 1024 \
                    -o "$_part_path" "$FULL_GGUF_URL" 2>&1; then
                if [[ ! -s "$_part_path" ]]; then
                    log "Download attempt $_attempt reported success but produced no partial file: $_part_path"
                else
                    acquire_model_lifecycle_lock || fail "Could not serialize full-model finalization."
                    if ! mv "$_part_path" "$_final_path"; then
                        log "Download attempt $_attempt failed while finalizing $_part_path -> $_final_path"
                        release_model_lifecycle_lock
                    fi
                fi
            else
                log "Download attempt $_attempt failed"
            fi
        fi

        if [[ ! -s "$_final_path" ]]; then
            continue
        fi

        if [[ "$TOTAL_BYTES" -gt 0 ]]; then
            ACTUAL_BYTES=$(file_size "$_final_path")
            if [[ "$ACTUAL_BYTES" -lt "$TOTAL_BYTES" ]]; then
                mv "$_final_path" "$_part_path" 2>/dev/null || rm -f "$_final_path"
                release_model_lifecycle_lock
                log "Downloaded model is smaller than expected (got $ACTUAL_BYTES, expected $TOTAL_BYTES); preserving as partial for retry."
                continue
            fi
            if [[ "$ACTUAL_BYTES" -gt "$TOTAL_BYTES" ]]; then
                rm -f "$_final_path" "$_part_path"
                release_model_lifecycle_lock
                log "Downloaded model is larger than expected (got $ACTUAL_BYTES, expected $TOTAL_BYTES); deleting corrupt file and retrying from scratch."
                continue
            fi
        fi

        write_status "verifying" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
        log "Download complete: $FULL_GGUF_FILE"
        if verify_model_integrity "$_final_path"; then
            _dl_success=true
            break
        fi

        rm -f "$_final_path" "$_part_path"
        release_model_lifecycle_lock
        log "Integrity verification failed on attempt $_attempt; retrying from a clean download."
    done

    kill $_monitor_pid 2>/dev/null || true
    trap - TERM INT

    if [[ "$_dl_success" != "true" ]]; then
        write_failed_download_status "$_part_path" "$TOTAL_BYTES" "Download failed after $_download_attempts attempts; partial file preserved for resume."
        fail "Download failed after $_download_attempts attempts. Preserved partial file for resume: $_part_path. Bootstrap model will continue running."
    fi
fi

# ── Phase 2: Verify integrity (if SHA256 provided) ──
if [[ -n "$FULL_GGUF_SHA256" ]]; then
    write_status "verifying" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
    log "Verifying SHA256..."
    if ! verify_model_integrity "$MODELS_DIR/$FULL_GGUF_FILE"; then
        rm -f "$MODELS_DIR/$FULL_GGUF_FILE"
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Downloaded model failed SHA256 after retries. Corrupt file deleted; bootstrap model left running."
        fail "SHA256 mismatch after retries. Deleted corrupt file."
    fi
fi

# Download bytes stay outside the lifecycle lock. Linux finalization acquires it
# immediately before publishing the full GGUF filename, then retains it through
# config promotion, compose verification, and bootstrap cleanup.
acquire_model_lifecycle_lock || fail "Could not serialize background full-model activation."

_windows_lemonade_swap_applies=false
_windows_native_llama_swap_applies=false
_docker_llama_swap_applies=false
if is_windows_bash; then
    _runtime_for_swap="$(read_env_value AMD_INFERENCE_RUNTIME | tr '[:upper:]' '[:lower:]')"
    _backend_for_swap="$(read_env_value LLM_BACKEND | tr '[:upper:]' '[:lower:]')"
    _managed_for_swap="$(read_env_value AMD_INFERENCE_MANAGED | tr '[:upper:]' '[:lower:]')"
    _runtime_mode_for_swap="$(read_env_value AMD_INFERENCE_RUNTIME_MODE | tr '[:upper:]' '[:lower:]')"
    _location_for_swap="$(read_env_value AMD_INFERENCE_LOCATION | tr '[:upper:]' '[:lower:]')"
    if [[ "$_runtime_for_swap" == "lemonade" || "$_backend_for_swap" == "lemonade" ]]; then
        _windows_lemonade_swap_applies=true
    elif [[ "$_managed_for_swap" != "false" && "$_location_for_swap" != "external" ]] \
        && [[ "$_runtime_mode_for_swap" == "windows-llama-server-fallback" || ( "$_runtime_for_swap" == "llama-server" && "$_location_for_swap" == "host" ) ]]; then
        _windows_native_llama_swap_applies=true
    fi
elif [[ -n "$DOCKER_CMD" ]]; then
    # Linux Docker installs mutate .env/models.ini before attempting a
    # llama-server hot-swap. Snapshot whenever Docker is available so every
    # Docker failure path can restore the last known-good model config.
    _docker_llama_swap_applies=true
fi

if [[ "$_windows_lemonade_swap_applies" == "true" || "$_windows_native_llama_swap_applies" == "true" || "$_docker_llama_swap_applies" == "true" ]]; then
    log "Snapshotting active model config before full-model swap..."
    _include_windows_lemonade_snapshot=false
    if [[ "$_windows_lemonade_swap_applies" == "true" ]]; then
        _include_windows_lemonade_snapshot=true
        capture_windows_lemonade_dependent_state
    fi
    if ! snapshot_active_model_config "$_include_windows_lemonade_snapshot"; then
        discard_active_model_config_snapshot
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model downloaded and verified, but ODS could not snapshot active model config before swap. Bootstrap model left unchanged; re-run to retry."
        exit 1
    fi
fi

# ── Phase 3: Update .env ──
write_status "swapping" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
log "Updating .env..."
if [[ -f "$ENV_FILE" ]]; then
    # Update GGUF_FILE
    if grep -q '^GGUF_FILE=' "$ENV_FILE"; then
        awk -v v="$FULL_GGUF_FILE" '{ if (index($0, "GGUF_FILE=") == 1) print "GGUF_FILE=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    # Update LLM_MODEL
    if grep -q '^LLM_MODEL=' "$ENV_FILE"; then
        awk -v v="$FULL_LLM_MODEL" '{ if (index($0, "LLM_MODEL=") == 1) print "LLM_MODEL=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    # Update MAX_CONTEXT / CTX_SIZE
    if grep -q '^MAX_CONTEXT=' "$ENV_FILE"; then
        awk -v v="$FULL_MAX_CONTEXT" '{ if (index($0, "MAX_CONTEXT=") == 1) print "MAX_CONTEXT=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    if grep -q '^CTX_SIZE=' "$ENV_FILE"; then
        awk -v v="$FULL_MAX_CONTEXT" '{ if (index($0, "CTX_SIZE=") == 1) print "CTX_SIZE=" v; else print }' \
            "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    fi
    log ".env updated"
else
    fail ".env not found at $ENV_FILE"
fi

# ── Phase 4: Update models.ini ──
log "Updating models.ini..."
mkdir -p "$(dirname "$MODELS_INI")"
cat > "$MODELS_INI" << EOF
[${FULL_LLM_MODEL}]
filename = ${FULL_GGUF_FILE}
load-on-startup = true
n-ctx = ${FULL_MAX_CONTEXT}
EOF
log "models.ini updated"

BOOTSTRAP_GGUF="${BOOTSTRAP_GGUF_FILE:-Qwen3.5-2B-Q4_K_M.gguf}"
BOOTSTRAP_PATH="$MODELS_DIR/$BOOTSTRAP_GGUF"
HOT_SWAP_VERIFIED=false

# ── Phase 5: Hot-swap llama-server (if running) ──
# Read OLLAMA_PORT from .env (nohup doesn't inherit env vars from parent)
if [[ -f "$ENV_FILE" ]]; then
    OLLAMA_PORT=$(grep -E '^OLLAMA_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
fi

if [[ "$_windows_lemonade_swap_applies" == "true" ]]; then
    if ! move_bootstrap_model_aside_for_windows_swap; then
        _move_restore_status="Previous active model config restore could not be verified; inspect the transaction snapshot."
        if restore_active_model_config; then
            _move_restore_status="Previous active model config restored; native Lemonade was not changed."
        fi
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model downloaded and verified, but ODS could not move the bootstrap model aside before the Windows Lemonade swap. ${_move_restore_status} Re-run to retry."
        exit 1
    fi

    if activate_windows_lemonade_full_model; then
        HOT_SWAP_VERIFIED=true
        discard_active_model_config_snapshot
        discard_bootstrap_model_backup_after_windows_swap
    else
        _windows_rollback_status="Rollback was attempted, but the previous model route was not proven; inspect the logs and active configs before retrying."
        if [[ "$WINDOWS_LEMONADE_ROLLBACK_VERIFIED" == "true" ]]; then
            _windows_rollback_status="Previous active model config restored and bootstrap model kept; dependents restarted and the previous model route verified."
        fi
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model downloaded and verified, but Windows Lemonade activation failed: ${WINDOWS_LEMONADE_SWAP_FAILURE}. ${_windows_rollback_status} Re-run to retry the swap."
        exit 1
    fi
elif [[ "$_windows_native_llama_swap_applies" == "true" ]]; then
    if restart_windows_native_llama_server_with_full_model; then
        if ! patch_hermes_model_after_swap; then
            log "Restoring previous active model config after Hermes patch failure..."
            restore_active_model_config || log "WARNING: could not restore active model config; inspect $ENV_FILE and $MODELS_INI"
            write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
                "Full model downloaded and loaded in native Windows llama-server, but the Hermes config patch failed after swap. Previous active model config restored; re-run to retry."
            exit 1
        fi
        if ! refresh_windows_native_litellm_local_config_after_swap; then
            log "Restoring previous active model config after LiteLLM config refresh failure..."
            restore_active_model_config || log "WARNING: could not restore active model config; inspect $ENV_FILE and $MODELS_INI"
            write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
                "Full model downloaded and loaded in native Windows llama-server, but the LiteLLM local config refresh failed after swap. Previous active model config restored; re-run to retry."
            exit 1
        fi
        HOT_SWAP_VERIFIED=true
        discard_active_model_config_snapshot
    else
        log "Restoring previous active model config after native Windows llama-server swap timeout..."
        restore_active_model_config || log "WARNING: could not restore active model config; inspect $ENV_FILE and $MODELS_INI"
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Model downloaded and verified, but native Windows llama-server did not load it after swap. Previous active model config restored and bootstrap model kept; re-run to retry the swap."
        exit 1
    fi
elif [[ -n "$DOCKER_CMD" ]] && $DOCKER_CMD ps --filter name=ods-llama-server --format '{{.Names}}' 2>/dev/null | grep -q ods-llama-server; then
    log "Restarting llama-server with full model..."

    # Read GPU backend from .env (needed for health endpoint and restart strategy)
    _gpu_backend=""
    if [[ -f "$ENV_FILE" ]]; then
        _gpu_backend=$(grep -E '^GPU_BACKEND=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
    fi

    # Detect compose files
    COMPOSE_ARGS=()
    if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
        read -ra COMPOSE_ARGS <<< "$(cat "$INSTALL_DIR/.compose-flags")"
    elif [[ -x "$INSTALL_DIR/scripts/resolve-compose-stack.sh" ]]; then
        _tier="1"
        _gpu_count="1"
        _ods_mode="local"
        if [[ -f "$ENV_FILE" ]]; then
            _tier=$(grep -E '^TIER=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
            [[ -n "$_tier" ]] || _tier="1"
            _gpu_count=$(grep -E '^GPU_COUNT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
            [[ -n "$_gpu_count" ]] || _gpu_count="1"
            _ods_mode=$(grep -E '^ODS_MODE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"\047\r')
            [[ -n "$_ods_mode" ]] || _ods_mode="local"
        fi
        # --gpu-count gates the multigpu-{backend} overlays and --ods-mode gates
        # the compose.local.yaml overlays. The resolver reads neither from the
        # environment, and the flags below are persisted to .compose-flags, so
        # omitting them writes a single-GPU local-mode stack over the real one.
        _resolved_env=$("$INSTALL_DIR/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "$_tier" \
            --gpu-backend "${_gpu_backend:-cpu}" \
            --gpu-count "$_gpu_count" \
            --ods-mode "$_ods_mode" \
            --env 2>/dev/null || true)
        _resolved_flags=$(printf '%s\n' "$_resolved_env" | sed -n 's/^COMPOSE_FLAGS="\([^"]*\)".*/\1/p')
        if [[ -n "$_resolved_flags" ]]; then
            read -ra COMPOSE_ARGS <<< "$_resolved_flags"
            printf '%s\n' "$_resolved_flags" > "$INSTALL_DIR/.compose-flags"
            log "Recovered compose flags via resolve-compose-stack.sh"
        fi
    elif [[ -f "$INSTALL_DIR/docker-compose.base.yml" ]]; then
        COMPOSE_ARGS=(-f "$INSTALL_DIR/docker-compose.base.yml")
        case "${_gpu_backend}" in
            nvidia) [[ -f "$INSTALL_DIR/docker-compose.nvidia.yml" ]] && COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.nvidia.yml") ;;
            amd)    [[ -f "$INSTALL_DIR/docker-compose.amd.yml" ]]    && COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.amd.yml") ;;
            apple)
                # On Darwin hosts the canonical macOS overlay lives at
                # installers/macos/docker-compose.macos.yml (native Metal llama-server
                # replicas: 0, llama-server-ready sidecar, host.docker.internal for
                # dashboard-api). The top-level docker-compose.apple.yml remains
                # valid for Linux hosts that select --gpu-backend apple (Lemonade).
                # Mirror the branch in scripts/resolve-compose-stack.sh so that the
                # .compose-flags fallback selects the same overlay the resolver does.
                if [[ "$(uname -s)" == "Darwin" && -f "$INSTALL_DIR/installers/macos/docker-compose.macos.yml" ]]; then
                    COMPOSE_ARGS+=(-f "$INSTALL_DIR/installers/macos/docker-compose.macos.yml")
                elif [[ -f "$INSTALL_DIR/docker-compose.apple.yml" ]]; then
                    COMPOSE_ARGS+=(-f "$INSTALL_DIR/docker-compose.apple.yml")
                fi
                ;;
            # cpu or unknown: base only, no GPU overlay
        esac
    fi

    cd "$INSTALL_DIR" || fail "Cannot cd to $INSTALL_DIR"

    # Restart llama-server — strategy depends on GPU backend:
    # - AMD (Lemonade): use 'restart' to preserve cached llama-server build.
    #   Lemonade reads models.ini at startup, so it picks up the new model.
    # - NVIDIA/CPU (llama.cpp): force-recreate so the new GGUF_FILE in .env
    #   takes effect. `compose stop` + `compose up -d` is NOT enough — when the
    #   service has a stopped container, compose will start the existing
    #   container in place (preserving its baked --model arg) instead of
    #   building a fresh one from the updated .env. The original CMD points at
    #   /models/${BOOTSTRAP_GGUF_FILE}, which Phase 4b just deleted, so the
    #   container crash-loops. `--force-recreate --no-deps` guarantees a new
    #   container; --no-deps avoids touching other services in the project.
    log "Restarting llama-server container (backend: ${_gpu_backend:-unknown})..."
    # Both backends need a container *recreate*, not just a restart, so the
    # updated CTX_SIZE / MAX_CONTEXT / GGUF_FILE env values from the
    # freshly-bumped .env land in the new container. A plain `compose
    # restart` preserves the original env vars from when the container
    # first started — which on AMD/Lemonade leaves LEMONADE_CTX_SIZE pinned
    # at the bootstrap-tier value (e.g. 8192) even after .env says 131072.
    # The lemonade-entrypoint.sh wrapper then sees the stale env value,
    # never updates /root/.cache/lemonade/config.json, and Lemonade serves
    # the full model at the bootstrap context size — Hermes then returns
    # empty responses in ~1s because its 16k-token tool catalog overruns
    # the 8k context window. OpenCode hits the same wall.
    #
    # `env -u` strips the model-config vars from compose's shell so the
    # freshly-updated .env wins interpolation. Compose precedence is
    # shell-env > .env > compose default, and Phase 11 (parent of this
    # nohup'd script) sets the bootstrap-tier values as shell variables.
    #
    # Named volumes (lemonade-cache / lemonade-llama / lemonade-recipe on
    # AMD) survive --force-recreate, so the Lemonade binary cache + HF
    # cache + recipe state all persist across the recreate. The older
    # "AMD uses restart to preserve cached binary" comment was wrong —
    # named volumes are decoupled from the container lifecycle.
    if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
        compose_recreate_llama_server_with_retry "${COMPOSE_ARGS[@]}" || \
            log "WARNING: llama-server recreate command failed after retries; continuing to health check before declaring failure."
    else
        # No reliable compose stack is available. Do NOT stop/remove the
        # currently-running bootstrap container here: leaving an old but
        # serving model online is safer than turning a completed download into
        # an outage. The operator can repair the compose cache or re-run the
        # installer; both paths can recreate llama-server from the updated .env.
        log "WARNING: unable to recover compose flags — leaving the current llama-server container untouched."
        log "Manual recovery: re-run the installer, or restore $INSTALL_DIR/.compose-flags and run the ODS CLI restart command."
        restore_active_model_config || log "WARNING: could not restore previous active model config; inspect $ENV_FILE and $MODELS_INI"
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model downloaded and verified, but ODS could not recover compose flags for Docker hot-swap. Previous active model config restored; re-run to retry."
        exit 1
    fi

    # Pick health endpoint based on GPU backend — Lemonade (AMD) serves
    # /api/v1/health, llama.cpp (NVIDIA/Apple/CPU) serves /health.
    if [[ "$_gpu_backend" == "amd" ]]; then
        _health_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/api/v1/health"
    else
        _health_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/health"
    fi

    # Wait for health (up to 5 minutes for the larger model to load)
    # For AMD/Lemonade: check that model_loaded is non-null in the JSON response.
    # Lemonade returns 200 with "model_loaded": null when no model is loaded yet.
    # Lemonade doesn't auto-load models from models.ini — it uses --extra-models-dir
    # for discovery but loads on-demand. We send a warm-up request to trigger loading.
    # For llama.cpp: a simple 200 check is sufficient — the server only starts
    # after loading the model specified in --model.
    log "Waiting for llama-server health at $_health_url ..."
    _healthy=false
    _warmup_sent=false
    for _i in $(seq 1 60); do
        _resp=$(curl -sf --max-time 5 "$_health_url" 2>/dev/null || echo "")
        if [[ -n "$_resp" ]]; then
            if [[ "$_gpu_backend" == "amd" ]]; then
                # Lemonade: verify a model is actually loaded, not just "status: ok"
                if echo "$_resp" | grep -q '"model_loaded"' && ! echo "$_resp" | grep -q '"model_loaded": *null'; then
                    _healthy=true
                    break
                fi
                # Lemonade is healthy but no model loaded — send a warm-up request
                # to trigger on-demand loading of the new model. Lemonade caches the
                # previously-loaded model name across restarts, which fails after the
                # bootstrap GGUF is deleted. This request forces it to load the new one.
                # Retry every 15s — the first request may fail if Lemonade isn't fully
                # ready to accept chat completions yet.
                if [[ "$_warmup_sent" == "false" ]] || (( _i % 3 == 0 )); then
                    # Escape any double-quotes in the filename so the JSON body
                    # below stays well-formed even for non-standard library entries.
                    # Mirrors the _safe_model pattern in write_status() above.
                    _model_id="$(read_env_value LEMONADE_MODEL)"
                    [[ -n "$_model_id" ]] || _model_id="extra.${FULL_GGUF_FILE//\"/\\\"}"
                    log "Sending warm-up request to trigger model loading: $_model_id (attempt $_i/60)"
                    if curl -sf --max-time 30 -X POST \
                        "http://127.0.0.1:${OLLAMA_PORT:-8080}/api/v1/chat/completions" \
                        -H "Content-Type: application/json" \
                        -d "{\"model\":\"${_model_id}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":1}" \
                        &>/dev/null; then
                        _warmup_sent=true
                        log "Warm-up request accepted — waiting for model to finish loading"
                    fi
                fi
                log "Lemonade healthy but no model loaded yet (attempt $_i/60)"
            else
                # llama.cpp: 200 means model is loaded
                _healthy=true
                break
            fi
        fi
        if [[ "$_gpu_backend" != "amd" ]] && docker_llama_server_container_failed_after_swap; then
            log "llama-server container exited or is restarting while loading the full model; treating Docker hot-swap as failed."
            break
        fi
        sleep 5
    done

    # Assert the recreated container's --model arg actually points at the new
    # GGUF file. If compose handed us back a started-not-recreated container
    # (the bug --force-recreate above is meant to prevent), the health check
    # may still pass on Lemonade (which loads on demand) but llama.cpp will
    # crash-loop the moment the next request hits, because Phase 4b has
    # already deleted the bootstrap GGUF the baked CMD refers to. Fail loudly
    # so the operator does not discover this hours later via a 502.
    if [[ "$_gpu_backend" != "amd" ]] && [[ -n "$DOCKER_CMD" ]]; then
        _running_cmd=$($DOCKER_CMD inspect ods-llama-server --format '{{join .Config.Cmd " "}}' 2>/dev/null || echo "")
        if [[ -z "$_running_cmd" ]]; then
            log "ERROR: could not inspect llama-server container command after recreate."
            log "  Recover with: cd $INSTALL_DIR && docker compose \$(cat .compose-flags) up -d --force-recreate --no-deps llama-server"
            write_status "failed"
            fail "llama-server command inspection failed after force-recreate."
        elif ! [[ "$_running_cmd" == *"/models/${FULL_GGUF_FILE}"* ]]; then
            log "ERROR: llama-server container started with stale --model arg."
            log "  expected /models/${FULL_GGUF_FILE}, got: $_running_cmd"
            log "  This means 'compose up -d --force-recreate' did not pick up the updated .env."
            # Dump what compose would have seen so a future regression can be
            # diagnosed from logs alone. If any of these have non-empty values,
            # they overrode the .env at compose interpolation time.
            for _k in GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE; do
                _v="$(printenv "$_k" 2>/dev/null || true)"
                if [[ -n "$_v" ]]; then
                    log "  shell env leak: $_k=$_v (overrode .env's $_k)"
                fi
            done
            log "  .env now has:"
            for _k in GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE; do
                log "    $(grep -E "^${_k}=" "$ENV_FILE" 2>/dev/null || echo "${_k}=<missing>")"
            done
            log "  Recover with: cd $INSTALL_DIR && env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE docker compose \$(cat .compose-flags) up -d --force-recreate --no-deps llama-server"
            write_status "failed"
            fail "llama-server container started with stale --model arg after force-recreate."
        fi
    fi

    if $_healthy; then
        log "SUCCESS: llama-server is running with $FULL_LLM_MODEL"
        HOT_SWAP_VERIFIED=true
        discard_active_model_config_snapshot
        # Regenerate lemonade.yaml with the new model ID and restart LiteLLM.
        # Lemonade exposes models as "extra.<GGUF_FILE>" — the config must
        # reference the exact ID, not a wildcard passthrough.
        if $DOCKER_CMD ps --filter name=ods-litellm --format '{{.Names}}' 2>/dev/null | grep -q ods-litellm; then
            _lemonade_model_id="$(read_env_value LEMONADE_MODEL)"
            [[ -n "$_lemonade_model_id" ]] || _lemonade_model_id="extra.${FULL_GGUF_FILE}"
            log "Updating LiteLLM config for new model: ${_lemonade_model_id}"
            # Read per-install lemonade key from .env; fall back to literal so
            # older installs without the key still produce a valid config (lemonade
            # itself ignores the value).
            LITELLM_LEMONADE_API_KEY=$(grep '^LITELLM_LEMONADE_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"\047\r')
            : "${LITELLM_LEMONADE_API_KEY:=sk-lemonade}"
            # extra_body.chat_template_kwargs.enable_thinking=false is what
            # un-blocks Perplexica + any other client that doesn't manually
            # prepend `/no_think` to its prompts. Qwen3 thinking models
            # (qwen3-coder-next, Qwen3.6-35B-A3B, Qwen3-30B-A3B) emit a
            # <think>...</think> reasoning block before the answer, which on
            # Strix Halo with a long Perplexica synthesis prompt can run for
            # minutes — the user sees the GPU spin up but no tokens reach the
            # UI until the close-think tag fires. Passing
            # `chat_template_kwargs: {enable_thinking: false}` to llama.cpp /
            # Lemonade hits the Qwen3 chat template's enable_thinking switch
            # and skips the reasoning block entirely.
            #
            # Verified 2026-05-20 on Strix Halo (AMD/Lemonade): same prompt
            # went from "hangs indefinitely" to 1.8s end-to-end with this
            # one block added. The kwarg is a Qwen3-specific switch and is
            # safely ignored by non-Qwen3 chat templates.
            _lemonade_api_base="http://llama-server:8080/api/v1"
            _amd_location="$(read_env_value AMD_INFERENCE_LOCATION | tr '[:upper:]' '[:lower:]')"
            _amd_port="$(read_env_value AMD_INFERENCE_PORT)"
            : "${_amd_port:=8080}"
            if [[ "$_amd_location" == "host" ]]; then
                _lemonade_api_base="http://host.docker.internal:${_amd_port}/api/v1"
            fi
            _renderer_ok=false
            _renderer_script="$INSTALL_DIR/scripts/render-runtime-configs.py"
            _renderer_py="${ODS_PYTHON_CMD:-}"
            if [[ -z "$_renderer_py" && -f "$INSTALL_DIR/lib/python-cmd.sh" ]]; then
                . "$INSTALL_DIR/lib/python-cmd.sh"
                _renderer_py="$(ods_detect_python_cmd 2>/dev/null || true)"
            fi
            if [[ -z "$_renderer_py" ]]; then
                _renderer_py="python3"
            fi
            if [[ -f "$_renderer_script" ]] && command -v "$_renderer_py" >/dev/null 2>&1; then
                if "$_renderer_py" "$_renderer_script" \
                    --surface litellm-lemonade \
                    --ods-mode lemonade \
                    --gpu-backend amd \
                    --gguf-file "$FULL_GGUF_FILE" \
                    --lemonade-model-id "$_lemonade_model_id" \
                    --lemonade-api-base "$_lemonade_api_base" \
                    --litellm-key "$LITELLM_LEMONADE_API_KEY" \
                    --output-root "$INSTALL_DIR" \
                    --write >/dev/null 2>&1; then
                    _renderer_ok=true
                else
                    log "WARNING: Runtime config renderer failed for LiteLLM; falling back to inline writer"
                fi
            fi
            if [[ "$_renderer_ok" != "true" ]]; then
                cat > "$INSTALL_DIR/config/litellm/lemonade.yaml" << LITELLM_UPGRADE_EOF
model_list:
  - model_name: default
    litellm_params:
      model: openai/${_lemonade_model_id}
      api_base: ${_lemonade_api_base}
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/${_lemonade_model_id}
      api_base: ${_lemonade_api_base}
      api_key: ${LITELLM_LEMONADE_API_KEY}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
LITELLM_UPGRADE_EOF
            fi
            unset _renderer_ok _renderer_script _renderer_py _lemonade_api_base _lemonade_model_id _amd_location _amd_port
            log "Restarting LiteLLM to pick up model change..."
            $DOCKER_CMD restart ods-litellm 2>&1 || log "WARNING: LiteLLM restart failed (non-fatal)"
        fi
        # Recreate OpenClaw so inject-token.js picks up the new GGUF_FILE/LLM_MODEL
        # from .env. A restart alone won't work — env vars are baked in at container
        # creation time, and inject-token.js builds the Lemonade model name from them.
        # Strip the same model-config vars here as the llama-server recreate path
        # so shell-env pollution cannot override the freshly-updated .env.
        if $DOCKER_CMD ps --filter name=ods-openclaw --format '{{.Names}}' 2>/dev/null | grep -q ods-openclaw; then
            log "Recreating OpenClaw to pick up model change..."
            # Guard on BOTH compose args AND a non-empty $DOCKER_COMPOSE_CMD —
            # mirrors the llama-server hot-swap contract above (the
            # `${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD"` checks).
            # If $DOCKER_COMPOSE_CMD is empty (no compose v2 plugin AND no
            # docker-compose v1 binary), expanding it as the command word
            # would turn the line into `"${COMPOSE_ARGS[@]}" up -d ...`,
            # which executes the first compose-arg (e.g. `-f`) as a binary.
            # Skip the recreate and surface a clear warning instead.
            if [[ ${#COMPOSE_ARGS[@]} -gt 0 && -n "$DOCKER_COMPOSE_CMD" ]]; then
                env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE \
                    $DOCKER_COMPOSE_CMD "${COMPOSE_ARGS[@]}" up -d --force-recreate openclaw 2>&1 || \
                    log "WARNING: OpenClaw recreate failed (non-fatal)"
            else
                log "WARNING: No compose binary available (DOCKER_COMPOSE_CMD empty or compose args missing) — OpenClaw was NOT recreated. The new model will not take effect until OpenClaw is recreated manually with: env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE docker compose up -d --force-recreate openclaw"
            fi
        fi
        # Patch Hermes Agent's config so it stops asking the LLM server for the
        # bootstrap model id. PR #1191 substitutes model.default in the template
        # at install time, but at install time we've only loaded the bootstrap
        # model (Qwen3.5-2B) — Hermes's /opt/data/config.yaml is therefore
        # pinned to that name. Once this script swaps Lemonade/llama-server
        # to the full model, Hermes keeps sending the stale bootstrap id and
        # every chat completion 404s.
        #
        # This is hard-broken on AMD/Lemonade (which strictly validates the
        # `model` field) and silently masked on NVIDIA/Apple (llama.cpp
        # ignores the field and serves whatever's loaded), so the bug
        # surfaces as "Hermes works on Tower2/Mac but every prompt 404s on
        # Strix Halo" after a bootstrap-to-full swap.
        #
        # Three files/views to keep in sync:
        #   1. data/hermes/config.yaml on the host — the bind-mounted live
        #      config that persists across Hermes restarts.
        #   2. /opt/data/config.yaml inside the container — the same live
        #      config from Hermes's view. Patch via docker exec too so Linux
        #      container-owned files can still be recovered.
        #   3. extensions/services/hermes/cli-config.yaml.template — the
        #      source Hermes copies into /opt/data on first start. Updating
        #      it keeps subsequent down-and-up cycles correct.
        # Lemonade prefixes the served model id with "extra."; llama.cpp
        # serves under the bare file name. Mirror the same branch PR #1191
        # added in installers/phases/11-services.sh.
        _hermes_old_model="$BOOTSTRAP_GGUF_FILE"
        _hermes_new_model="$FULL_GGUF_FILE"
        _hermes_base_url="$(read_env_value HERMES_LLM_BASE_URL)"
        _gpu_backend_for_hermes=$(grep -E '^GPU_BACKEND=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "")
        if [[ "$_gpu_backend_for_hermes" == "amd" ]]; then
            _hermes_old_model="extra.$BOOTSTRAP_GGUF_FILE"
            _hermes_new_model="$(read_env_value LEMONADE_MODEL)"
            [[ -n "$_hermes_new_model" ]] || _hermes_new_model="extra.$FULL_GGUF_FILE"
        fi
        log "Patching Hermes config: model.default $_hermes_old_model -> $_hermes_new_model"
        _hermes_request_timeout=180
        _hermes_llm_backend_for_timeout="$(read_env_value LLM_BACKEND | tr '[:upper:]' '[:lower:]')"
        if is_windows_bash || [[ "$_gpu_backend_for_hermes" == "amd" || "$_hermes_llm_backend_for_timeout" == "lemonade" ]]; then
            _hermes_request_timeout=900
        fi

        # Template on host (user-owned, no sudo needed). Patch this even when
        # Hermes is stopped so future container creates do not copy the stale
        # bootstrap model id.
        _hermes_tpl="$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template"
        if [[ -f "$_hermes_tpl" ]]; then
            if ! patch_hermes_yaml_with_sed "$_hermes_tpl" "$_hermes_new_model" "$FULL_MAX_CONTEXT" "$_hermes_base_url" "$_hermes_request_timeout"; then
                log "WARNING: Could not patch ${_hermes_tpl} (non-fatal; operator can hand-edit before restarting Hermes)"
            fi
        fi

        _hermes_live="$INSTALL_DIR/data/hermes/config.yaml"
        _hermes_live_host_patched=false
        if [[ -f "$_hermes_live" ]]; then
            if patch_hermes_yaml_with_sed "$_hermes_live" "$_hermes_new_model" "$FULL_MAX_CONTEXT" "$_hermes_base_url" "$_hermes_request_timeout"; then
                _hermes_live_host_patched=true
            else
                log "WARNING: Could not patch ${_hermes_live} on host (non-fatal if container patch below succeeds)"
            fi
        fi

        if $DOCKER_CMD ps --filter name=ods-hermes --format '{{.Names}}' 2>/dev/null | grep -q ods-hermes; then
            # Live config inside the running container (owned by container UID).
            _hermes_new_model_sed="$(printf '%s' "$_hermes_new_model" | sed 's/[\\&|]/\\&/g')"
            _hermes_base_url_sed="$(printf '%s' "$_hermes_base_url" | sed 's/[\\&|]/\\&/g')"
            _hermes_live_patch="sed -i -e 's|^  default: \".*\"[[:space:]]*$|  default: \"${_hermes_new_model_sed}\"|' -e 's|^  context_length: .*|  context_length: ${FULL_MAX_CONTEXT}|' -e 's|^    context_length: .*|    context_length: ${FULL_MAX_CONTEXT}|'"
            if [[ -n "$_hermes_base_url" ]]; then
                _hermes_live_patch="${_hermes_live_patch} -e 's|^  base_url: \".*\"|  base_url: \"${_hermes_base_url_sed}\"|'"
            fi
            if [[ "$_hermes_request_timeout" != "180" ]]; then
                _hermes_live_patch="${_hermes_live_patch} -e 's|^    request_timeout_seconds: 180[[:space:]]*$|    request_timeout_seconds: ${_hermes_request_timeout}|'"
            fi
            _hermes_live_patch="${_hermes_live_patch} -e 's|^  enabled: .*|  enabled: true|' -e 's|^  threshold: .*|  threshold: 0.75|' -e 's|^  target_ratio: .*|  target_ratio: 0.50|' -e 's|^  protect_last_n: .*|  protect_last_n: 40|' /opt/data/config.yaml"
            $DOCKER_CMD exec ods-hermes sh -c \
                "$_hermes_live_patch" 2>&1 || \
                log "WARNING: Could not patch Hermes /opt/data/config.yaml (non-fatal — operator can hand-edit and 'docker restart ods-hermes')"
            log "Restarting Hermes to pick up model change..."
            $DOCKER_CMD restart ods-hermes 2>&1 || log "WARNING: Hermes restart failed (non-fatal — hand-restart with 'docker restart ods-hermes')"

            # Pre-warm the freshly-swapped LLM + Hermes's 14K-token system prompt.
            #
            # Two latency hits if we skip this:
            #   1. llama-server / Lemonade loads the full model into VRAM on first
            #      request (`--n-gpu-layers 999` is lazy). PR #1192 already warms
            #      this at install time, but that warm-up was against the
            #      bootstrap model — after the swap, the slot is cold again.
            #   2. Hermes's runtime config bakes a 14K-token system prompt
            #      (skills, soul, tool descriptors). First Hermes prompt has
            #      to prefill all of it. Empirically 67s on Strix Halo,
            #      1m25s on macOS, ~5s once cached. We've seen real users
            #      think Hermes is broken because they alt-tabbed away during
            #      a fresh install and the first prompt looked stuck.
            #
            # Mirrors PR #1192's pattern: best-effort, time-bounded, never fails
            # the upgrade. If either warm-up times out the swap still succeeds —
            # the user just eats the slow first call.
            log "Pre-warming llama-server slot with full model..."
            _prewarm_api_path="/v1"
            _prewarm_model="$FULL_GGUF_FILE"
            if [[ "$_gpu_backend_for_hermes" == "amd" ]]; then
                _prewarm_api_path="/api/v1"
                _prewarm_model="$(read_env_value LEMONADE_MODEL)"
                [[ -n "$_prewarm_model" ]] || _prewarm_model="extra.$FULL_GGUF_FILE"
            fi
            _prewarm_body="{\"model\":\"${_prewarm_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}"
            if $DOCKER_CMD exec ods-hermes curl -sf --max-time 120 -X POST \
                "http://llama-server:8080${_prewarm_api_path}/chat/completions" \
                -H "Content-Type: application/json" \
                -d "$_prewarm_body" >/dev/null 2>&1; then
                log "llama-server slot pre-warmed."
            else
                log "WARNING: llama-server pre-warm timed out — first Hermes prompt may be slow."
            fi

            # Wait for Hermes to come back up after the restart, then trigger
            # one no-op invocation so the 14K system prompt gets into
            # llama-server's KV cache. We cap at 90s total — long enough for
            # Hermes's skills sync + config bootstrap (start_period: 60s in
            # compose.yaml) plus a few decode tokens, short enough that a
            # broken Hermes doesn't stall the script forever.
            log "Pre-warming Hermes system prompt (caches 14K-token prefill)..."
            _hermes_ready=false
            for _i in $(seq 1 30); do
                if $DOCKER_CMD exec ods-hermes curl -sf --max-time 3 http://127.0.0.1:9119/api/status >/dev/null 2>&1; then
                    _hermes_ready=true
                    break
                fi
                sleep 2
            done
            if $_hermes_ready; then
                if $DOCKER_CMD exec ods-hermes timeout 90 \
                    /opt/hermes/.venv/bin/hermes -z "ping" --yolo \
                    >/dev/null 2>&1; then
                    log "Hermes system prompt cached — first user prompt will be fast."
                else
                    log "WARNING: Hermes warm-up timed out (>90s). First user prompt will incur the full 14K-token prefill."
                fi
            else
                log "WARNING: Hermes did not respond on /api/status within 60s; skipping system-prompt warm-up."
            fi
        else
            if [[ -f "$_hermes_live" && "$_hermes_live_host_patched" != "true" ]]; then
                log "WARNING: Hermes is stopped and ${_hermes_live} could not be patched; operator can hand-edit and restart Hermes"
            fi
        fi
        sync_windows_opencode_config
    else
        log "WARNING: llama-server health check timed out. The model may still be loading."
        log "Check: docker logs ods-llama-server"
        _rollback_status="Previous active model config restore was attempted; inspect docker logs ods-llama-server and re-run to retry."
        if restore_docker_llama_server_after_swap_failure "$_health_url"; then
            _rollback_status="Previous active model config restored and llama-server is healthy; re-run to retry the full-model swap."
        fi
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model downloaded and verified, but Docker llama-server did not become healthy after the hot-swap. ${_rollback_status}"
        exit 1
    fi
elif [[ -f "$INSTALL_DIR/data/.llama-server.pid" ]]; then
    # macOS native llama-server (Metal) — restart with new model
    log "Detected native llama-server (macOS Metal mode)"

    LLAMA_SERVER_BIN="$INSTALL_DIR/bin/llama-server"
    LLAMA_SERVER_PID_FILE="$INSTALL_DIR/data/.llama-server.pid"
    LLAMA_SERVER_LOG="$INSTALL_DIR/data/llama-server.log"

    if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
        log "WARNING: llama-server binary not found at $LLAMA_SERVER_BIN. Cannot hot-swap."
        log "Run './ods-macos.sh restart' to load the new model manually."
    else
        # Read updated model config from .env
        _gguf_file=$(grep '^GGUF_FILE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'")
        _ctx_size=$(grep '^CTX_SIZE=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'" || echo "")
        [[ -z "$_ctx_size" ]] && _ctx_size=$(grep '^MAX_CONTEXT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'" || echo "")
        [[ -z "$_ctx_size" ]] && _ctx_size="16384"
        _model_path="$MODELS_DIR/${_gguf_file}"

        if [[ ! -f "$_model_path" ]]; then
            log "WARNING: Model file not found at $_model_path"
        else
            # Capture old model path for rollback before we kill the process
            _old_pid=$(cat "$LLAMA_SERVER_PID_FILE" 2>/dev/null | tr -d '[:space:]')
            _old_model_path=""
            if [[ -n "$_old_pid" ]] && kill -0 "$_old_pid" 2>/dev/null; then
                _old_model_path=$(ps -p "$_old_pid" -o args= 2>/dev/null | grep -oE '\-\-model [^ ]+' | awk '{print $2}') || true
            fi

            # Stop existing native llama-server
            if [[ -n "$_old_pid" ]] && kill -0 "$_old_pid" 2>/dev/null; then
                # Verify it's actually llama-server (PID could have been reused)
                if ps -p "$_old_pid" -o comm= 2>/dev/null | grep -q llama; then
                    log "Stopping native llama-server (PID $_old_pid)..."
                    kill "$_old_pid" 2>/dev/null || true
                    sleep 2
                    if kill -0 "$_old_pid" 2>/dev/null; then
                        kill -9 "$_old_pid" 2>/dev/null || true
                    fi
                else
                    log "PID $_old_pid is no longer llama-server, skipping kill"
                fi
            fi

            # Read reasoning mode from .env (default off to prevent thinking models
            # from consuming the entire token budget on internal reasoning)
            _reasoning=$(grep '^LLAMA_REASONING=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "")
            [[ -z "$_reasoning" ]] && _reasoning="off"
            case "$_reasoning" in
                off)  _reasoning_fmt="none" ;;
                on)   _reasoning_fmt="deepseek" ;;
                *)    _reasoning_fmt="$_reasoning" ;;
            esac

            # Honour the unified BIND_ADDRESS knob (PR #964); empty/missing → loopback.
            _bind=$(grep '^BIND_ADDRESS=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            [[ -z "$_bind" ]] && _bind="127.0.0.1"
            _native_port=$(grep '^ODS_NATIVE_LLAMA_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            [[ "$_native_port" =~ ^[0-9]+$ ]] || _native_port="8080"
            _flash_attn=$(grep '^LLAMA_ARG_FLASH_ATTN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _cache_type_k=$(grep '^LLAMA_ARG_CACHE_TYPE_K=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _cache_type_v=$(grep '^LLAMA_ARG_CACHE_TYPE_V=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _n_cpu_moe=$(grep '^LLAMA_ARG_N_CPU_MOE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _spec_type=$(grep '^LLAMA_ARG_SPEC_TYPE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _spec_draft_n_max=$(grep '^LLAMA_ARG_SPEC_DRAFT_N_MAX=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
            _llama_args=(
                --host "$_bind" --port "$_native_port"
                --model "$_model_path"
                --ctx-size "$_ctx_size"
                --n-gpu-layers 999
                --reasoning-format "$_reasoning_fmt"
                --metrics
            )
            [[ -n "$_flash_attn" ]] && _llama_args+=(--flash-attn "$_flash_attn")
            [[ -n "$_cache_type_k" ]] && _llama_args+=(--cache-type-k "$_cache_type_k")
            [[ -n "$_cache_type_v" ]] && _llama_args+=(--cache-type-v "$_cache_type_v")
            [[ -n "$_n_cpu_moe" ]] && _llama_args+=(--n-cpu-moe "$_n_cpu_moe")
            [[ -n "$_spec_type" ]] && _llama_args+=(--spec-type "$_spec_type")
            [[ -n "$_spec_draft_n_max" ]] && _llama_args+=(--spec-draft-n-max "$_spec_draft_n_max")

            # Relaunch with new model
            log "Starting native llama-server with ${_gguf_file}..."
            (
                cd "$INSTALL_DIR" || exit 1
                exec "$LLAMA_SERVER_BIN" "${_llama_args[@]}"
            ) > "$LLAMA_SERVER_LOG" 2>&1 &
            _new_pid=$!
            echo "$_new_pid" > "$LLAMA_SERVER_PID_FILE"

            # Wait for health
            log "Waiting for native llama-server health..."
            _healthy=false
            for _i in $(seq 1 60); do
                if curl -sf --max-time 5 "http://127.0.0.1:${_native_port}/health" &>/dev/null; then
                    _healthy=true
                    break
                fi
                sleep 5
            done

            if $_healthy; then
                log "SUCCESS: Native llama-server running with ${_gguf_file} (PID $_new_pid)"
                HOT_SWAP_VERIFIED=true
            else
                log "WARNING: New model failed to load. Attempting rollback..."
                kill "$_new_pid" 2>/dev/null || true
                sleep 2
                if kill -0 "$_new_pid" 2>/dev/null; then
                    kill -9 "$_new_pid" 2>/dev/null || true
                fi
                if [[ -n "${_old_model_path:-}" && -f "$_old_model_path" ]]; then
                    (
                        cd "$INSTALL_DIR" || exit 1
                        exec "$LLAMA_SERVER_BIN" \
                            --host "$_bind" --port "$_native_port" \
                            --model "$_old_model_path" \
                            --ctx-size "$_ctx_size" \
                            --n-gpu-layers 999 \
                            --reasoning-format "${_reasoning_fmt:-none}" \
                            --metrics
                    ) > "$LLAMA_SERVER_LOG" 2>&1 &
                    _rollback_pid=$!
                    echo "$_rollback_pid" > "$LLAMA_SERVER_PID_FILE"
                    log "Rolled back to previous model: $(basename "$_old_model_path") (PID $_rollback_pid)"
                else
                    log "WARNING: Could not rollback — previous model not found."
                    log "Run './ods-macos.sh restart' to manually recover."
                fi
                write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
                    "Full model downloaded and verified, but native macOS llama-server did not load it after swap. Bootstrap model kept; run './ods-macos.sh restart' or re-run to retry."
                exit 1
            fi
        fi
    fi
else
    log "Docker services not running. Config updated — full model will load on next start."
    discard_active_model_config_snapshot
fi

# ── Phase 5b: Remove bootstrap model only after verified full-model serving ──
# Lemonade's --extra-models-dir auto-discovers all GGUFs in /models. Removing
# the bootstrap too early can wedge Windows Lemonade: it may keep serving the
# old model id while the file is gone, producing 500s until a manual restart.
# Keep the bootstrap as the recovery path unless the new model has answered a
# real completion.
if [[ "$HOT_SWAP_VERIFIED" == "true" && -f "$BOOTSTRAP_PATH" && "$FULL_GGUF_FILE" != "$BOOTSTRAP_GGUF" ]]; then
    log "Removing bootstrap model after verified full-model serving: $BOOTSTRAP_GGUF"
    rm -f "$BOOTSTRAP_PATH"
    log "Bootstrap model removed"
    if ! refresh_lemonade_after_bootstrap_cleanup; then
        write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 \
            "Full model served, but ODS could not refresh Lemonade after removing the bootstrap model. Re-run to retry."
        fail "Lemonade refresh after bootstrap cleanup failed."
    fi
elif [[ "$FULL_GGUF_FILE" != "$BOOTSTRAP_GGUF" && -f "$BOOTSTRAP_PATH" ]]; then
    log "Keeping bootstrap model until the full model is verified serving: $BOOTSTRAP_GGUF"
fi

# ── Phase 5c: Update Perplexica's defaultChatModel ──
# Phase 12 of the installer configures Perplexica with whatever LLM_MODEL was
# in scope at that time — which on bootstrap installs is the bootstrap model
# name (e.g. qwen3.5-2b), NOT the full model. Without an update here,
# Perplexica's settings.preferences.defaultChatModel stays "qwen3.5-2b"
# forever, even after the hot-swap replaces the underlying GGUF. The UI
# shows the wrong model name in the dropdown, and the chatModels list under
# the OpenAI provider keeps the bootstrap entry instead of the full model.
#
# Requests still functionally route via LiteLLM's `*` wildcard or llama.cpp's
# served-model-passthrough, so this hasn't been a hard failure — but it's a
# cosmetic + future-proofing issue (a non-wildcard router would 404 the
# stale model id). Mirror the install-time logic from
# installers/phases/12-health.sh:194-238: update modelProviders + preferences
# via Perplexica's `/api/config` PUT endpoint.
PERPLEXICA_PORT=$(grep -E '^PERPLEXICA_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r')
: "${PERPLEXICA_PORT:=3004}"
_perplexica_url="http://127.0.0.1:${PERPLEXICA_PORT}"
if curl -sf --max-time 3 "${_perplexica_url}/api/config" >/dev/null 2>&1; then
    log "Updating Perplexica config to point at ${FULL_LLM_MODEL}..."
        _py_cmd="${ODS_PYTHON_CMD:-}"
        if [[ -z "$_py_cmd" && -f "$INSTALL_DIR/lib/python-cmd.sh" ]]; then
            . "$INSTALL_DIR/lib/python-cmd.sh"
            _py_cmd="$(ods_detect_python_cmd 2>/dev/null || true)"
        fi
        if [[ -z "$_py_cmd" ]]; then
            _py_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
        fi
    if [[ -n "$_py_cmd" ]]; then
        # On Lemonade, LiteLLM exposes the model id as "extra.<GGUF_FILE>".
        # On NVIDIA/Apple/CPU, llama.cpp serves under the bare GGUF id.
        _px_model="$FULL_GGUF_FILE"
        _runtime_for_perplexica=$(grep -E '^AMD_INFERENCE_RUNTIME=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' | tr '[:upper:]' '[:lower:]' || echo "")
        _llm_backend_for_perplexica=$(grep -E '^LLM_BACKEND=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' | tr '[:upper:]' '[:lower:]' || echo "")
        if [[ "$_runtime_for_perplexica" == "lemonade" || "$_llm_backend_for_perplexica" == "lemonade" ]]; then
            _px_model="$(read_env_value LEMONADE_MODEL)"
            [[ -n "$_px_model" ]] || _px_model="extra.$FULL_GGUF_FILE"
        fi
        _litellm_key=$(grep -E '^LITELLM_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "no-key")
        : "${_litellm_key:=no-key}"
        if [[ "$_runtime_for_perplexica" == "lemonade" || "$_llm_backend_for_perplexica" == "lemonade" ]]; then
            _px_base_url="$(read_env_value HERMES_LLM_BASE_URL)"
            : "${_px_base_url:=http://litellm:4000/v1}"
        else
            _px_base_url=$(grep -E '^LLM_API_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"\047\r' || echo "http://llama-server:8080")
            : "${_px_base_url:=http://llama-server:8080}"
        fi
        case "$_px_base_url" in
            */v1|*/api/v1) ;;
            *) _px_base_url="${_px_base_url%/}/v1" ;;
        esac
        if _px_update_output=$(curl -sf --max-time 3 "${_perplexica_url}/api/config" 2>/dev/null | \
            PERPLEXICA_URL="$_perplexica_url" \
            PX_MODEL="$_px_model" \
            PX_KEY="$_litellm_key" \
            PX_BASE_URL="$_px_base_url" \
            "$_py_cmd" -c '
import os, sys, json, urllib.request
config = json.load(sys.stdin)["values"]
providers = config.get("modelProviders", [])
openai_prov = next((p for p in providers if p["type"] == "openai"), None)
if not openai_prov:
    sys.exit(0)  # Perplexica has no OpenAI provider configured; skip (non-fatal)
url = os.environ["PERPLEXICA_URL"] + "/api/config"
model = os.environ["PX_MODEL"]
key = os.environ["PX_KEY"]
base_url = os.environ["PX_BASE_URL"]
def post(k, v):
    data = json.dumps({"key": k, "value": v}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)
def post_setup_complete():
    setup_url = os.environ["PERPLEXICA_URL"] + "/api/config/setup-complete"
    req = urllib.request.Request(setup_url, data=b"{}", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        post("setupComplete", True)
openai_prov["chatModels"] = [{"key": model, "name": model}]
# Refresh the provider config so Perplexica can reach the active ODS route.
prov_config = openai_prov.get("config") or {}
prov_config["apiKey"] = key
prov_config["baseURL"] = base_url
openai_prov["config"] = prov_config
post("modelProviders", providers)
prefs = config.get("preferences", {})
prefs["defaultChatModel"] = model
prefs["defaultChatProvider"] = openai_prov["id"]
post("preferences", prefs)
post_setup_complete()
print("ok")
 ' 2>&1); then
            log "Perplexica defaultChatModel updated to ${_px_model}."
        else
            log "Perplexica updater error: $(printf '%s' "$_px_update_output" | tr '\r\n' ' ' | tail -c 800)"
            log "WARNING: Perplexica config update failed (non-fatal — defaultChatModel may still read the bootstrap value)"
        fi
    else
        log "WARNING: python3 not found, skipping Perplexica config update"
    fi
fi

# ── Phase 6: Restart host agent (if running) ──
# The host agent may cache stale state — restart it so it picks up the new
# model config and any updated endpoints.
if command -v systemctl &>/dev/null && systemctl --user is-active ods-host-agent.service &>/dev/null; then
    log "Restarting ods-host-agent (systemd)..."
    systemctl --user restart ods-host-agent.service 2>&1 || \
        log "WARNING: Could not restart host agent (non-fatal)"
elif [[ -f "$HOME/Library/LaunchAgents/com.ods.host-agent.plist" ]]; then
    log "Restarting ods-host-agent (launchctl)..."
    launchctl kickstart -k "gui/$(id -u)/com.ods.host-agent" 2>&1 || \
        log "WARNING: Could not restart host agent (non-fatal)"
fi

write_status "complete" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""
log "Bootstrap upgrade complete."
