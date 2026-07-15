#!/bin/bash
# ============================================================================
# ODS Installer — Phase 13: Summary & Desktop Shortcut
# ============================================================================
# Part of: installers/phases/
# Purpose: Display URLs, create desktop shortcut, pin to sidebar, write
#          summary JSON, run preflight validation
#
# Expects: DRY_RUN, INSTALL_DIR, SCRIPT_DIR, LOG_FILE, INTERACTIVE,
#           TIER, TIER_NAME, VERSION, GPU_BACKEND, LLM_MODEL, OFFLINE_MODE,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_QDRANT, ENABLE_HERMES, ENABLE_OPENCLAW,
#           COMPOSE_FLAGS, SUMMARY_JSON_FILE, PREFLIGHT_REPORT_FILE,
#           BGRN, GRN, AMB, WHT, NC, DASHBOARD_PORT (:-3001),
#           CAP_HARDWARE_CLASS_ID (:-unknown), CAP_HARDWARE_CLASS_LABEL (:-Unknown),
#           BACKEND_SERVICE_NAME (:-llama-server),
#           show_success_card(), bootline(), signal(), ai_ok(), log()
# Provides: Desktop shortcut, sidebar pin, summary JSON
#
# Modder notes:
#   Change the final banner, add new service URLs, or modify the desktop
#   shortcut here.
# ============================================================================

ods_progress 98 "summary" "Finishing up"

# Source service registry for port resolution
. "$SCRIPT_DIR/lib/service-registry.sh"
sr_load

# Resolve port overrides from .env (same as phase 12)
if [[ -f "$INSTALL_DIR/.env" ]]; then
    . "$SCRIPT_DIR/lib/safe-env.sh" 2>/dev/null || true
    load_env_file "$INSTALL_DIR/.env"
    sr_resolve_ports
fi

# Get local IP for LAN access
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")

# Mode is now stored in .env as ODS_MODE (set by phase 06)
if ! $DRY_RUN; then
    mkdir -p "$INSTALL_DIR"
else
    log "[DRY RUN] Would write mode metadata to $INSTALL_DIR"
fi

# Show the cinematic success card
show_success_card "http://localhost:3000" "http://localhost:3001" "$LOCAL_IP"

# Mark the setup wizard as already completed for fresh installs. The
# dashboard-api reads this file (container path /data/config/setup-complete.json,
# mounted from ${INSTALL_DIR}/data) to decide first_run state; without it the
# wizard reappears on every visit after a fresh install. Non-fatal — if the
# write fails, the wizard simply shows once.
if ! $DRY_RUN; then
    _setup_config_dir="${INSTALL_DIR}/data/config"
    _setup_complete_file="${_setup_config_dir}/setup-complete.json"
    _completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    if mkdir -p "${_setup_config_dir}" 2>/dev/null \
        && printf '{"completed_at": "%s", "version": "1.0.0"}\n' "${_completed_at}" > "${_setup_complete_file}" 2>/dev/null \
        && chmod 644 "${_setup_complete_file}" 2>/dev/null; then
        log "Setup wizard pre-marked complete at ${_setup_complete_file}"
    else
        ai_warn "Could not write ${_setup_complete_file} (non-fatal)"
    fi
fi

# Check background tasks before showing additional info
if [[ -f "$SCRIPT_DIR/installers/lib/background-tasks.sh" ]]; then
    . "$SCRIPT_DIR/installers/lib/background-tasks.sh"

    # Check if any background tasks are registered
    if [[ -f "$BG_TASK_REGISTRY" ]]; then
        echo ""
        ai "Checking background tasks..."
        bg_task_summary >> "$LOG_FILE" 2>&1

        # Check SDXL Lightning download specifically
        if bg_task_status "sdxl-download" &>/dev/null; then sdxl_status=0; else sdxl_status=$?; fi
        if [[ $sdxl_status -ne 3 ]]; then
            case $sdxl_status in
                0)  # Still running
                    ai_warn "SDXL Lightning model download still in progress"
                    ai "ComfyUI image generation will be available once download completes"
                    ai "Check progress: tail -f $INSTALL_DIR/logs/sdxl-download.log"
                    ;;
                1)  # Completed
                    ai_ok "SDXL Lightning model download completed"
                    ;;
                2)  # Failed
                    ai_warn "SDXL Lightning model download encountered errors"
                    ai "Check log: $INSTALL_DIR/logs/sdxl-download.log"
                    ;;
            esac
        fi
    fi
fi

# Check bootstrap model upgrade status
if [[ "${_BOOTSTRAP_ACTIVE:-false}" == "true" ]]; then
    if bg_task_status "full-model-download" &>/dev/null; then _upgrade_status=0; else _upgrade_status=$?; fi
    case $_upgrade_status in
        0)  # Still running
            echo ""
            ai_warn "Using bootstrap model ($BOOTSTRAP_LLM_MODEL). Full model ($FULL_LLM_MODEL) downloading..."
            ai "The model will auto-swap when ready. Check: tail -f $INSTALL_DIR/logs/model-upgrade.log"
            ;;
        1)  # Completed
            ai_ok "Full model ($FULL_LLM_MODEL) downloaded and swapped"
            ;;
        2)  # Failed
            ai_warn "Full model download failed. Currently running bootstrap model ($BOOTSTRAP_LLM_MODEL)"
            ai "Re-run installer to retry, or check: $INSTALL_DIR/logs/model-upgrade.log"
            ;;
    esac
fi


# Additional service info
bootline
echo -e "${BGRN}ALL SERVICES${NC}"
bootline
# Core services always shown
echo "  • Chat UI:       http://localhost:${SERVICE_PORTS[open-webui]:-3000}"
echo "  • Dashboard:     http://localhost:${SERVICE_PORTS[dashboard]:-3001}"
echo "  • LLM API:       http://localhost:${SERVICE_PORTS[llama-server]:-11434}/v1  (llama-server)"
[[ "${ENABLE_PERPLEXICA:-false}" == "true" ]] && echo "  • Perplexica:    http://localhost:${SERVICE_PORTS[perplexica]:-3004}"
[[ "${ENABLE_COMFYUI:-false}" == "true" ]] && echo "  • ComfyUI:       http://localhost:${SERVICE_PORTS[comfyui]:-8188}"
[[ "$ENABLE_HERMES" == "true" ]] && echo "  • Hermes (auth): http://localhost:${SERVICE_PORTS[hermes-proxy]:-9120}  (magic-link gated; not direct :9119)"
[[ "$ENABLE_OPENCLAW" == "true" ]] && echo "  • OpenClaw:      http://localhost:${SERVICE_PORTS[openclaw]:-7860}"
systemctl --user is-active opencode-web &>/dev/null && echo "  • OpenCode:      http://localhost:3003"
[[ "$ENABLE_VOICE" == "true" ]] && echo "  • Whisper STT:   http://localhost:${SERVICE_PORTS[whisper]:-9000}"
[[ "$ENABLE_VOICE" == "true" ]] && echo "  • TTS (Kokoro):  http://localhost:${SERVICE_PORTS[tts]:-8880}"
[[ "$ENABLE_WORKFLOWS" == "true" ]] && echo "  • n8n:           http://localhost:${SERVICE_PORTS[n8n]:-5678}"
[[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && echo "  • Qdrant:        http://localhost:${SERVICE_PORTS[qdrant]:-6333}"
echo ""

# Configuration summary
bootline
echo -e "${BGRN}YOUR CONFIGURATION${NC}"
bootline
echo "  • Tier: $TIER ($TIER_NAME)"
echo "  • Model: $LLM_MODEL"
echo "  • Install dir: $INSTALL_DIR"
echo ""

# Quick commands
bootline
echo -e "${BGRN}QUICK COMMANDS${NC}"
bootline
echo "  cd $INSTALL_DIR"
echo "  docker compose ps                          # Check container status"
echo "  docker compose logs -f                     # View container logs"
echo "  docker compose restart                     # Restart containers"
echo "  systemctl --user list-timers               # Check maintenance timers"
echo "  ods status                                 # Check service health"
echo ""

if [[ -f "$LOG_FILE" ]]; then
    echo -e "${BGRN}Full installation log:${NC} $LOG_FILE"
    echo ""
fi
if [[ -f "$PREFLIGHT_REPORT_FILE" ]]; then
    echo -e "${BGRN}Preflight report:${NC} $PREFLIGHT_REPORT_FILE"
    echo ""
fi

# Run preflight check to validate installation
echo ""
bootline
echo -e "${BGRN}RUNNING PREFLIGHT VALIDATION${NC}"
bootline
echo ""

if [[ -f "$SCRIPT_DIR/ods-preflight.sh" ]]; then
    # Services like APE and Embeddings may still be starting on fresh installs.
    # Retry up to 3 times with 10s backoff before reporting failures.
    _preflight_passed=false
    for _pf_attempt in 1 2 3; do
        if bash "$SCRIPT_DIR/ods-preflight.sh" 2>>"$LOG_FILE"; then
            _preflight_passed=true
            break
        fi
        if [[ $_pf_attempt -lt 3 ]]; then
            ai_warn "Some services still starting (attempt $_pf_attempt/3). Retrying in 10s..."
            sleep 10
        fi
    done
    if [[ "$_preflight_passed" != "true" ]]; then
        ai_warn "Preflight did not fully pass. Services may still be starting."
        ai "  Check with: ods status"
    fi
else
    log "Preflight script not found — skipping validation"
fi

# Extension manifest validation (non-blocking)
echo ""
bootline
echo -e "${BGRN}VALIDATING EXTENSIONS${NC}"
bootline
echo ""
if [[ -f "$SCRIPT_DIR/scripts/validate-manifests.sh" ]]; then
    if bash "$SCRIPT_DIR/scripts/validate-manifests.sh"; then
        ai_ok "Extension manifests validated for this ODS version."
    else
        warn "Extension manifest validation reported issues. See details above."
    fi
else
    log "Extension validation script not found — skipping extension checks"
fi

# Non-core extension runtime check (Docker + optional HTTP health; non-blocking)
echo ""
bootline
echo -e "${BGRN}EXTENSION RUNTIME CHECK${NC}"
bootline
echo ""
if [[ -f "$SCRIPT_DIR/scripts/extension-runtime-check.sh" ]]; then
    bash "$SCRIPT_DIR/scripts/extension-runtime-check.sh" "$INSTALL_DIR" || true
else
    log "extension-runtime-check.sh not found — skipping"
fi

#=============================================================================
# Desktop Shortcut & Sidebar Pin
#=============================================================================
if ! $DRY_RUN; then
    DESKTOP_FILE="$HOME/.local/share/applications/ods.desktop"
    mkdir -p "$HOME/.local/share/applications"
    cat > "$DESKTOP_FILE" << DESKTOP_EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=ODS
Comment=Local AI Dashboard
Exec=xdg-open http://localhost:3001
Icon=applications-internet
Terminal=false
Categories=Development;
StartupNotify=true
DESKTOP_EOF

    # Pin to GNOME sidebar (favorites) if gsettings is available
    if command -v gsettings &> /dev/null; then
        CURRENT_FAVS=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo "[]")
        if [[ "$CURRENT_FAVS" != *"ods.desktop"* ]]; then
            NEW_FAVS=$(echo "$CURRENT_FAVS" | sed "s/]$/, 'ods.desktop']/" | sed "s/\[, /[/")
            gsettings set org.gnome.shell favorite-apps "$NEW_FAVS" 2>/dev/null || true
            ai_ok "Dashboard pinned to sidebar"
        fi
    fi

    ai_ok "Desktop shortcut created: ODS"
fi

#=============================================================================
# Bash Completion Setup
#=============================================================================
if ! $DRY_RUN; then
    COMPLETION_FILE="$INSTALL_DIR/completions/ods-cli.bash"
    if [[ -f "$COMPLETION_FILE" ]]; then
        # Add completion sourcing to .bashrc if not already present
        if ! grep -q "ods-cli.bash" "$HOME/.bashrc" 2>/dev/null; then
            cat >> "$HOME/.bashrc" << 'BASHRC_EOF'

# ODS CLI bash completion
if [[ -f "$HOME/ods/completions/ods-cli.bash" ]]; then
    . "$HOME/ods/completions/ods-cli.bash"
fi
BASHRC_EOF
            ai_ok "Bash completion enabled for ods-cli"
        fi
    fi
fi

#=============================================================================
# Symlink ods CLI to PATH
#=============================================================================
if ! $DRY_RUN; then
    if [[ -x "$INSTALL_DIR/ods-cli" ]]; then
        if ! command -v ods &>/dev/null; then
            if sudo -n ln -sf "$INSTALL_DIR/ods-cli" /usr/local/bin/ods 2>/dev/null; then
                ai_ok "ods command installed (try: ods status)"
            else
                # Fallback: user-local bin directory (no sudo needed)
                mkdir -p "$HOME/.local/bin"
                if ln -sf "$INSTALL_DIR/ods-cli" "$HOME/.local/bin/ods" 2>/dev/null; then
                    ai_ok "ods command installed to ~/.local/bin/ods"
                    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
                        ai_warn "Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
                    fi
                else
                    ai_warn "Could not create 'ods' command. Add manually:"
                    ai "  sudo ln -sf $INSTALL_DIR/ods-cli /usr/local/bin/ods"
                fi
            fi
        else
            ai_ok "ods command already available"
        fi
    fi
fi

#=============================================================================
# Post-Install Validation
#=============================================================================
if ! $DRY_RUN; then
    # Check Perplexica config was seeded (phase 12 may have failed silently)
    if $DOCKER_CMD inspect ods-perplexica &>/dev/null; then
        _perplexica_model="${LLM_MODEL:-qwen3-30b-a3b}"
        if [[ -n "${GGUF_FILE:-}" ]]; then
            _perplexica_model="$GGUF_FILE"
            _perplexica_backend="$(printf '%s' "${LLM_BACKEND:-${AMD_INFERENCE_RUNTIME:-}}" | tr '[:upper:]' '[:lower:]')"
            if [[ "$_perplexica_backend" == "lemonade" ]]; then
                _perplexica_model="extra.$GGUF_FILE"
            fi
        fi
        _perplexica_status=$(curl -sf --max-time 5 "http://127.0.0.1:${SERVICE_PORTS[perplexica]:-3004}/api/config" 2>>"$LOG_FILE" | \
            PERPLEXICA_MODEL="$_perplexica_model" "$PYTHON_CMD" -c '
import os, sys, json
values = json.load(sys.stdin).get("values", {})
model = os.environ["PERPLEXICA_MODEL"]
providers = values.get("modelProviders", [])
openai_prov = next((p for p in providers if p.get("type") == "openai"), {})
chat_models = openai_prov.get("chatModels") or []
prefs = values.get("preferences") or {}
has_model = any(m.get("key") == model or m.get("name") == model for m in chat_models)
print("ok" if values.get("setupComplete") and has_model and prefs.get("defaultChatModel") == model else "needed")
' 2>>"$LOG_FILE" || echo "skip")
        if [[ "$_perplexica_status" == "needed" ]]; then
            ai_warn "Perplexica config incomplete — running auto-setup..."
            if [[ -x "$INSTALL_DIR/scripts/repair/repair-perplexica.sh" ]]; then
                PERPLEXICA_MODEL="$_perplexica_model" bash "$INSTALL_DIR/scripts/repair/repair-perplexica.sh" \
                    "http://127.0.0.1:${SERVICE_PORTS[perplexica]:-3004}" \
                    "${LLM_MODEL:-qwen3-30b-a3b}" >> "$LOG_FILE" 2>&1 && \
                    ai_ok "Perplexica configured" || \
                    ai_warn "Perplexica may need manual config at :${SERVICE_PORTS[perplexica]:-3004}"
            fi
        fi
    fi

    # Check render/video groups for AMD GPU users
    if [[ "${GPU_BACKEND:-}" == "amd" ]]; then
        if ! groups 2>/dev/null | grep -qE "\b(render|video)\b"; then
            echo ""
            echo -e "${AMB}┌──────────────────────────────────────────────────────────────┐${NC}"
            echo -e "${AMB}│  AMD GPU: user not in render/video groups                    │${NC}"
            echo -e "${AMB}│  GPU-accelerated services (ComfyUI, ROCm) may not work.      │${NC}"
            echo -e "${AMB}│                                                              │${NC}"
            echo -e "${AMB}│  Fix: sudo usermod -aG render,video \$USER                    │${NC}"
            echo -e "${AMB}│  Then log out and back in.                                   │${NC}"
            echo -e "${AMB}└──────────────────────────────────────────────────────────────┘${NC}"
        fi
    fi
fi

if command -v ods_readiness_summary >/dev/null 2>&1; then
    _dashboard_url="http://localhost:${SERVICE_PORTS[dashboard]:-3001}"
    {
        printf 'Dashboard|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[dashboard]:-3001}" "${SERVICE_HEALTH[dashboard]:-/}" "$(sr_container dashboard)" "$_dashboard_url"
        printf 'Chat UI (Open WebUI)|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[open-webui]:-3000}" "${SERVICE_HEALTH[open-webui]:-/}" "$(sr_container open-webui)" "http://localhost:${SERVICE_PORTS[open-webui]:-3000}"
        printf 'llama-server|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[llama-server]:-8080}" "${SERVICE_HEALTH[llama-server]:-/health}" "$(sr_container llama-server)" "http://localhost:${SERVICE_PORTS[llama-server]:-8080}/v1"
        printf 'Dashboard API|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[dashboard-api]:-3002}" "${SERVICE_HEALTH[dashboard-api]:-/health}" "$(sr_container dashboard-api)" "http://localhost:${SERVICE_PORTS[dashboard-api]:-3002}"
        printf 'LiteLLM|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[litellm]:-4000}" "${SERVICE_HEALTH[litellm]:-/health/readiness}" "$(sr_container litellm)" "http://localhost:${SERVICE_PORTS[litellm]:-4000}"
        [[ "${ENABLE_PERPLEXICA:-false}" == "true" ]] && printf 'Perplexica|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[perplexica]:-3004}" "${SERVICE_HEALTH[perplexica]:-/}" "$(sr_container perplexica)" "http://localhost:${SERVICE_PORTS[perplexica]:-3004}"
        [[ "$ENABLE_OPENCLAW" == "true" ]] && printf 'OpenClaw|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[openclaw]:-7860}" "${SERVICE_HEALTH[openclaw]:-/}" "$(sr_container openclaw)" "http://localhost:${SERVICE_PORTS[openclaw]:-7860}"
        [[ "$ENABLE_VOICE" == "true" ]] && printf 'Whisper (STT)|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[whisper]:-9000}" "${SERVICE_HEALTH[whisper]:-/health}" "$(sr_container whisper)" "http://localhost:${SERVICE_PORTS[whisper]:-9000}"
        [[ "$ENABLE_VOICE" == "true" ]] && printf 'Kokoro (TTS)|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[tts]:-8880}" "${SERVICE_HEALTH[tts]:-/health}" "$(sr_container tts)" "http://localhost:${SERVICE_PORTS[tts]:-8880}"
        [[ "$ENABLE_WORKFLOWS" == "true" ]] && printf 'n8n|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[n8n]:-5678}" "${SERVICE_HEALTH[n8n]:-/healthz}" "$(sr_container n8n)" "http://localhost:${SERVICE_PORTS[n8n]:-5678}"
        [[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && printf 'Qdrant|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[qdrant]:-6333}" "${SERVICE_HEALTH[qdrant]:-/}" "$(sr_container qdrant)" "http://localhost:${SERVICE_PORTS[qdrant]:-6333}"
        [[ "${ENABLE_COMFYUI:-}" == "true" ]] && printf 'ComfyUI|http://127.0.0.1:%s%s|%s|%s\n' \
            "${SERVICE_PORTS[comfyui]:-8188}" "${SERVICE_HEALTH[comfyui]:-/}" "$(sr_container comfyui)" "http://localhost:${SERVICE_PORTS[comfyui]:-8188}"
        # Ensure the block exits 0 regardless of the trailing optional conditionals:
        # under set -e + pipefail, a false `[[ ENABLE_x ]] && printf` makes the block
        # return 1, which propagates through the pipe and trips the ERR trap.
        :
    } | ods_readiness_summary "ods status" "$LOG_FILE" "$_dashboard_url"
fi

echo ""
signal "Broadcast stable. You're free now."
echo ""
DASHBOARD_PORT="${SERVICE_PORTS[dashboard]:-3001}"
WEBUI_PORT="${SERVICE_PORTS[open-webui]:-3000}"
OPENCLAW_PORT="${SERVICE_PORTS[openclaw]:-7860}"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
echo -e "${GRN}──────────────────────────────────────────────────────────────────────────────${NC}"
echo -e "${BGRN}  YOUR ODS IS LIVE${NC}"
echo -e "${GRN}──────────────────────────────────────────────────────────────────────────────${NC}"
echo ""
echo -e "  ${BGRN}Dashboard${NC}    ${WHT}http://localhost:${DASHBOARD_PORT}${NC}"
echo -e "  ${BGRN}Chat${NC}         ${WHT}http://localhost:${WEBUI_PORT}${NC}"
[[ "$ENABLE_HERMES" == "true" ]] && \
echo -e "  ${BGRN}Hermes${NC}       ${WHT}http://localhost:${SERVICE_PORTS[hermes-proxy]:-9120}${NC}  ${AMB}(magic-link gated)${NC}"
[[ "$ENABLE_OPENCLAW" == "true" ]] && \
echo -e "  ${BGRN}OpenClaw${NC}     ${WHT}http://localhost:${OPENCLAW_PORT}${NC}"
systemctl --user is-active opencode-web &>/dev/null && \
echo -e "  ${BGRN}OpenCode${NC}     ${WHT}http://localhost:3003${NC}"
echo ""
if [[ -n "$LOCAL_IP" ]]; then
    _bind=$(grep "^BIND_ADDRESS=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "127.0.0.1")
    [[ -z "$_bind" ]] && _bind="127.0.0.1"
    if [[ "$_bind" == "0.0.0.0" ]]; then
        echo -e "  ${AMB}On your network:${NC}  ${WHT}http://${LOCAL_IP}:${DASHBOARD_PORT}${NC}"
    else
        echo -e "  ${AMB}LAN access:${NC}      ${DIM}Reinstall with --lan or set BIND_ADDRESS=0.0.0.0 in .env${NC}"
    fi
fi
echo ""
echo -e "  Start here → ${WHT}http://localhost:${DASHBOARD_PORT}${NC}"
echo -e "  The Dashboard shows all services, GPU status, and quick links."
echo ""
echo -e "${GRN}──────────────────────────────────────────────────────────────────────────────${NC}"
echo ""

if [[ -n "$SUMMARY_JSON_FILE" ]]; then
    PYTHON_CMD="python3"
    if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
        . "$SCRIPT_DIR/lib/python-cmd.sh"
        PYTHON_CMD="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    "$PYTHON_CMD" - "$SUMMARY_JSON_FILE" "$VERSION" "$INSTALL_DIR" "$TIER" "$TIER_NAME" "$GPU_BACKEND" "${BACKEND_SERVICE_NAME:-llama-server}" "$LLM_MODEL" "$COMPOSE_FLAGS" "$DRY_RUN" "$PREFLIGHT_REPORT_FILE" "${CAP_HARDWARE_CLASS_ID:-unknown}" "${CAP_HARDWARE_CLASS_LABEL:-Unknown}" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

(
    out_file,
    version,
    install_dir,
    tier,
    tier_name,
    gpu_backend,
    backend_service,
    llm_model,
    compose_flags,
    dry_run,
    preflight_report,
    hw_class_id,
    hw_class_label,
) = sys.argv[1:]

payload = {
    "version": "1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "installer_version": version,
    "install_dir": install_dir,
    "tier": {"id": tier, "name": tier_name},
    "runtime": {
        "gpu_backend": gpu_backend,
        "backend_service": backend_service,
        "llm_model": llm_model,
        "compose_flags": compose_flags,
        "dry_run": dry_run == "true",
    },
    "hardware_class": {"id": hw_class_id, "label": hw_class_label},
    "preflight_report": preflight_report,
}

path = pathlib.Path(out_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"[INFO] Wrote installer summary JSON: {out_file}")
PY
fi
