#!/usr/bin/env bash
# Shared post-Pixel optional setup for normal installation and retained recovery.

_write_macos_opencode_config() {
    local config_path="$1" model_name="$2" base_url="$3" api_key="$4" context_length="$5"
    # OpenCode reads config.json, not opencode.json, so the same document has
    # to land in both files — matching installers/phases/07-devtools.sh on
    # Linux and installers/windows/lib/opencode-config.ps1 on Windows.
    local compat_path
    compat_path="$(dirname "$config_path")/config.json"
    mkdir -p "$(dirname "$config_path")"
    ODS_OPENCODE_MODEL="$model_name" \
    ODS_OPENCODE_BASE_URL="$base_url" \
    ODS_OPENCODE_API_KEY="$api_key" \
    ODS_OPENCODE_CONTEXT="$context_length" \
        /usr/bin/python3 - "$config_path" "$compat_path" <<'OPENCODE_CONFIG_PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
compat_path = Path(sys.argv[2])
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}

model_name = os.environ["ODS_OPENCODE_MODEL"]
base_url = os.environ["ODS_OPENCODE_BASE_URL"]
api_key = os.environ["ODS_OPENCODE_API_KEY"]
context = int(os.environ["ODS_OPENCODE_CONTEXT"])
if context < 1024:
    raise SystemExit("OpenCode requires at least 1024 context tokens")
output_limit = min(32768, context // 4)
provider_id = "llama-server"
provider = data.setdefault("provider", {}).setdefault(provider_id, {})
provider.update({
    "npm": "@ai-sdk/openai-compatible",
    "name": "ODS inference",
    "options": {"baseURL": base_url, "apiKey": api_key},
    "models": {
        model_name: {
            "name": model_name,
            "limit": {"context": context, "output": output_limit},
        }
    },
})
data["model"] = f"{provider_id}/{model_name}"
data.setdefault("$schema", "https://opencode.ai/config.json")

payload = json.dumps(data, indent=2) + "\n"


def write_atomic(target):
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


for target in (path, compat_path):
    write_atomic(target)

    check = json.loads(target.read_text(encoding="utf-8"))
    check_provider = check["provider"][provider_id]
    if check.get("model") != f"{provider_id}/{model_name}":
        raise SystemExit(f"OpenCode model verification failed for {target.name}")
    if check_provider["options"] != {"baseURL": base_url, "apiKey": api_key}:
        raise SystemExit(f"OpenCode route verification failed for {target.name}")
OPENCODE_CONFIG_PY
}

_opencode_candidate_is_file() {
    local candidate="$1"
    [[ -n "$candidate" && "$candidate" == /* && -x "$candidate" && ! -d "$candidate" ]]
}

_find_opencode_bin() {
    local candidate="" brew_prefix=""
    for candidate in "${OPENCODE_BIN:-}" "$HOME/.opencode/bin/opencode"; do
        if _opencode_candidate_is_file "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix 2>/dev/null || true)"
        candidate="${brew_prefix:+${brew_prefix}/bin/opencode}"
        if _opencode_candidate_is_file "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    candidate="$(type -P opencode 2>/dev/null || true)"
    if _opencode_candidate_is_file "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

_install_opencode() {
    OPENCODE_BIN="$(_find_opencode_bin 2>/dev/null || true)"
    # shellcheck source=../../lib/opencode-runtime.sh
    . "$SCRIPT_DIR/../lib/opencode-runtime.sh"
    if OPENCODE_BIN="$(ods_install_opencode "$OPENCODE_BIN")"; then
        ai_ok "Reviewed OpenCode release installed ($OPENCODE_BIN)"
    else
        OPENCODE_BIN=""
        ai_warn "OpenCode upgrade failed; existing binary/configuration preserved. Re-run after resolving the download or binary error."
        return 1
    fi
}

ods_macos_install_opencode() {
    local strict="${1:-false}"
    if $ENABLE_OPENCODE; then
        if [[ -e "$OPENCODE_PLIST" || -L "$OPENCODE_PLIST" ]] && ! ods_macos_opencode_plist_owned \
            "$OPENCODE_PLIST" "$OPENCODE_PLIST_LABEL" "$OPENCODE_BUN_TMPDIR"; then
            ai_err "A foreign OpenCode plist uses the ODS path; leaving it untouched."
            return 1
        fi
        if launchctl print "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" >/dev/null 2>&1 \
            && ! ods_macos_opencode_loaded_owned "$OPENCODE_PLIST" "$OPENCODE_PLIST_LABEL" \
                "$OPENCODE_BUN_TMPDIR" "$(id -u)"; then
            ai_err "A foreign OpenCode service uses the ODS label; leaving it untouched."
            return 1
        fi
    fi
    # ── Install & start OpenCode only when selected ──
    if $OPENCODE_DISABLE_EXPLICIT || $OPENCODE_DISABLE_SELECTED; then
        if ods_macos_opencode_plist_owned \
            "$OPENCODE_PLIST" "$OPENCODE_PLIST_LABEL" "$OPENCODE_BUN_TMPDIR"; then
            if launchctl print "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" >/dev/null 2>&1 \
                && ! ods_macos_opencode_loaded_owned "$OPENCODE_PLIST" "$OPENCODE_PLIST_LABEL" \
                    "$OPENCODE_BUN_TMPDIR" "$(id -u)"; then
                ai_warn "A foreign OpenCode service uses the ODS label; leaving it untouched."
            else
                launchctl disable "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" || {
                    ai_err "Could not disable the ODS OpenCode login service."
                    exit 1
                }
                ai "Disabled future OpenCode login starts; any current session remains running."
            fi
        fi
    fi
    if $ENABLE_OPENCODE; then
    chapter "OPENCODE (AI CODING IDE)"

    if ! _install_opencode; then
        [[ "$strict" != true ]] || return 1
    fi

    # OpenCode is native, so cloud mode uses LiteLLM's published host port while
    # local mode follows the actual native llama bind and port.
    if [[ -n "$OPENCODE_BIN" && -x "$OPENCODE_BIN" ]]; then
        mkdir -p "$OPENCODE_CONFIG_DIR"
        _opencode_switchboard_mode="$(read_env_value "$INSTALL_DIR/.env" "ODS_MODEL_SWITCHBOARD")"
        if [[ "${_opencode_switchboard_mode:-enabled}" == "enabled" ]]; then
            _opencode_model="ods/current"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="4000"
            _opencode_bind="127.0.0.1"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        elif $CLOUD_MODE; then
            _opencode_model="default"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="4000"
            _opencode_bind="127.0.0.1"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        else
            _opencode_model="$LLM_MODEL"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "ODS_NATIVE_LLAMA_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="8080"
            _opencode_bind="127.0.0.1"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="no-key"
        fi
        if [[ -z "$_opencode_api_key" ]] \
           || ! _write_macos_opencode_config \
                "$OPENCODE_CONFIG_DIR/opencode.json" \
                "$_opencode_model" "$_opencode_base_url" "$_opencode_api_key" \
                "${MAX_CONTEXT:-32768}"; then
            ai_err "Could not configure OpenCode for the active inference route."
            exit 1
        fi
        ai_ok "OpenCode configured for ${_opencode_model} at ${_opencode_base_url}"
        unset _opencode_model _opencode_port _opencode_bind _opencode_host \
            _opencode_switchboard_mode \
            _opencode_base_url _opencode_api_key

        # Install as macOS LaunchAgent (auto-start on login).
        # Log path is intentionally decoupled from INSTALL_DIR: xpcproxy denies
        # file-write-create on non-$HOME volumes, which causes the launchd spawn
        # to exit 78 before the target process ever runs. $HOME/Library/Logs is
        # always inside xpcproxy's sandbox writable set, so use that instead.
        mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS"
        OPENCODE_LAUNCHD_PATH="$(_compute_launchd_path "$(dirname "$OPENCODE_BIN")")"
        cat > "$OPENCODE_PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${OPENCODE_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <!-- OpenCode 1.18.x (Bun 1.3.14) copies bundled native libraries to a
             new temp file on every load and never deletes them
             (anomalyco/opencode#42700, #49283). Empty the ODS-owned
             BUN_TMPDIR on every start, then exec OpenCode itself. -->
        <string>/bin/sh</string>
        <string>-c</string>
        <string>dir="\$1"; shift; rm -rf "\$dir" &amp;&amp; mkdir -p -m 0700 "\$dir" &amp;&amp; export BUN_TMPDIR="\$dir" &amp;&amp; exec "\$@"</string>
        <string>ods-opencode-web</string>
        <string>${OPENCODE_BUN_TMPDIR}</string>
        <string>${OPENCODE_BIN}</string>
        <string>web</string>
        <string>--port</string>
        <string>3003</string>
        <string>--hostname</string>
        <string>127.0.0.1</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${OPENCODE_LAUNCHD_PATH}</string>
        <key>OPENCODE_ENABLE_EXA</key>
        <string>1</string>
        <!-- Preserve inherited OPENCODE_WEBSEARCH_PROVIDER; Exa is the default. -->
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/ODS/opencode-web.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/ODS/opencode-web.log</string>
</dict>
</plist>
PLIST_EOF

        # Unload existing (if any) and load new plist. bootout legitimately
        # errors when no service is loaded, so we keep that suppressed; the
        # bootstrap call surfaces real failures (e.g. launchd throttle EIO).
        launchctl enable "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" || {
            ai_err "Could not enable the ODS OpenCode login service."
            exit 1
        }
        launchctl bootout "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" >/dev/null 2>&1 || true
        _opencode_bootstrap_err="$(launchctl bootstrap "gui/$(id -u)" "$OPENCODE_PLIST" 2>&1)" && _opencode_bootstrap_rc=0 || _opencode_bootstrap_rc=$?
        if [[ $_opencode_bootstrap_rc -eq 0 ]]; then
            ai_ok "OpenCode Web UI service installed (LaunchAgent, port 3003)"
        else
            ai_warn "OpenCode LaunchAgent failed (rc=${_opencode_bootstrap_rc}): ${_opencode_bootstrap_err}"
            ai_warn "Start manually: ${OPENCODE_BIN} web --port 3003"
            [[ "$strict" != true ]] || return 1
        fi
        if [[ "$strict" == true ]]; then
            local attempt ready=false
            for attempt in $(seq 1 20); do
                if ods_macos_opencode_loaded_owned "$OPENCODE_PLIST" "$OPENCODE_PLIST_LABEL" \
                    "$OPENCODE_BUN_TMPDIR" "$(id -u)" \
                    && launchctl print "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" 2>/dev/null \
                        | grep -Eq '^[[:space:]]*state = running[[:space:]]*$' \
                    && curl -fsS --connect-timeout 2 --max-time 5 \
                        "http://127.0.0.1:${OPENCODE_PORT}" >/dev/null 2>&1; then
                    ready=true
                    break
                fi
                sleep 1
            done
            if [[ "$ready" != true ]]; then
                ai_err "The selected OpenCode login service did not become ready."
                return 1
            fi
        fi
    elif [[ "$strict" == true ]]; then
        ai_err "The selected OpenCode binary is unavailable."
        return 1
    fi
    fi
    return 0
}

ods_macos_prepare_voice() {
    local strict="${1:-false}"
# ── Pre-download the Whisper STT model ──
# Speaches does NOT auto-download on transcription requests — it returns 404.
# We must trigger the download explicitly here, verify it completed, and
# surface a clear recovery command if anything fails.
if [[ "$ENABLE_VOICE" == "true" ]]; then
    # Read AUDIO_STT_MODEL from .env (written by env-generator). On macOS the
    # default is base; user can override by editing .env before reinstalling.
    STT_MODEL=$(grep -m1 '^AUDIO_STT_MODEL=' "${INSTALL_DIR}/.env" 2>/dev/null \
                | cut -d= -f2- | tr -d '"' | tr -d '\r' || true)
    [[ -z "$STT_MODEL" ]] && STT_MODEL="Systran/faster-whisper-base"
    STT_MODEL_ENCODED="${STT_MODEL//\//%2F}"
    # macOS reassigns Whisper to 9100 if another service owns port 9000.
    WHISPER_PORT_RESOLVED="${WHISPER_PORT:-9000}"
    WHISPER_URL="http://127.0.0.1:${WHISPER_PORT_RESOLVED}"
    STT_MODEL_URL="${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}"
    STT_TRIGGER_TIMEOUT_SECONDS="${ODS_STT_TRIGGER_TIMEOUT_SECONDS:-30}"
    STT_CACHE_WAIT_SECONDS="${ODS_STT_CACHE_WAIT_SECONDS:-900}"
    [[ "$STT_TRIGGER_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || STT_TRIGGER_TIMEOUT_SECONDS=30
    [[ "$STT_CACHE_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || STT_CACHE_WAIT_SECONDS=900
    STT_RECOVERY_CMD="curl --max-time ${STT_TRIGGER_TIMEOUT_SECONDS} -X POST ${STT_MODEL_URL}"

    _macos_stt_model_cached() {
        local _url="$1"
        curl -sf --max-time 10 "$_url" &>/dev/null
    }

    _trigger_macos_stt_model_download() {
        local _url="$1"
        local _rc=0

        # Speaches can keep downloading after the request is accepted. Keep the
        # client bounded, then use the cache endpoint as the strict source of truth.
        curl -sS --fail --max-time "${STT_TRIGGER_TIMEOUT_SECONDS}" -X POST "$_url" \
            >> "$ODS_LOG_FILE" 2>&1 || _rc=$?
        if [[ "$_rc" -eq 0 || "$_rc" -eq 28 ]]; then
            return 0
        fi
        ai_warn "STT model download trigger returned curl exit ${_rc}; verifying cache before failing."
        return 1
    }

    _wait_macos_stt_model_cached() {
        local _url="$1"
        local _deadline=$((SECONDS + STT_CACHE_WAIT_SECONDS))

        while (( SECONDS < _deadline )); do
            if _macos_stt_model_cached "$_url"; then
                return 0
            fi
            sleep 5
        done
        _macos_stt_model_cached "$_url"
    }

    # Step 1: wait briefly for the models API to be ready (max 15s).
    _stt_api_ready=false
    for _i in $(seq 1 15); do
        if curl -sf --max-time 2 "${WHISPER_URL}/v1/models" &>/dev/null; then
            _stt_api_ready=true
            break
        fi
        sleep 1
    done

    if ! $_stt_api_ready; then
        ai_warn "STT models API not ready -- download manually:"
        echo "    $STT_RECOVERY_CMD"
        [[ "$strict" != true ]] || return 1
    # Step 2: skip if already cached.
    elif _macos_stt_model_cached "$STT_MODEL_URL"; then
        ai_ok "STT model already cached (${STT_MODEL})"
    else
        # Step 3: POST to trigger download.
        ai "Downloading STT model (${STT_MODEL})..."
        _trigger_macos_stt_model_download "$STT_MODEL_URL" || true

        # Step 4: verify the model is actually cached.
        if _wait_macos_stt_model_cached "$STT_MODEL_URL"; then
            ai_ok "STT model cached (${STT_MODEL})"
        else
            ai_warn "STT model download failed -- run manually:"
            echo "    $STT_RECOVERY_CMD"
            echo "    See $ODS_LOG_FILE for details."
            [[ "$strict" != true ]] || return 1
        fi
    fi
fi

    return 0
}

ods_macos_configure_perplexica() {
# ── Auto-configure Perplexica ──
if $ENABLE_PERPLEXICA; then
    ai "Configuring Perplexica..."
    PERPLEXICA_MODEL="${GGUF_FILE:-$LLM_MODEL}"
    PERPLEXICA_API_KEY="no-key"
    PERPLEXICA_BASE_URL="${CONTAINER_LLM_URL:-http://host.docker.internal:8080}"
    _perplexica_switchboard_mode="$(read_env_value "$INSTALL_DIR/.env" "ODS_MODEL_SWITCHBOARD")"
    if [[ "${_perplexica_switchboard_mode:-enabled}" == "enabled" ]]; then
        PERPLEXICA_MODEL="ods/current"
        PERPLEXICA_API_KEY="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        PERPLEXICA_BASE_URL="http://litellm:4000"
    fi
    $CLOUD_MODE && PERPLEXICA_MODEL="default"
    if $CLOUD_MODE; then
        PERPLEXICA_API_KEY="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        PERPLEXICA_BASE_URL="http://litellm:4000"
    fi
    _perplexica_port="$(read_env_value "$INSTALL_DIR/.env" "PERPLEXICA_PORT")"
    [[ "$_perplexica_port" =~ ^[0-9]+$ ]] || _perplexica_port="3004"
    if [[ -z "$PERPLEXICA_API_KEY" ]] \
       || ! configure_perplexica "$_perplexica_port" "$PERPLEXICA_MODEL" \
            "$PERPLEXICA_BASE_URL" "$PERPLEXICA_API_KEY"; then
        ai_err "Perplexica was selected but its authenticated inference route could not be configured and verified."
        exit 1
    fi
    ai_ok "Perplexica configured (model: ${PERPLEXICA_MODEL})"
    unset PERPLEXICA_API_KEY PERPLEXICA_BASE_URL _perplexica_port _perplexica_switchboard_mode
fi

    return 0
}
