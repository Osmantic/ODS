#!/bin/bash
# ODS Installer entrypoint (PR-1 dispatcher)
# Pass-through options (implemented in install-core.sh):
# --dry-run --skip-docker --force --tier --voice --workflows --rag
# --openclaw --all --non-interactive --no-bootstrap --bootstrap --offline
# --use-existing-lemonade --lemonade-url --lemonade-api-key
# This dispatcher also owns --tui, an opt-in configuration-first menu that
# compiles the user's choices into the existing non-interactive CLI contract.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/installers/dispatch.sh"

target="$(resolve_installer_target)"

_tui_read() {
    local variable="$1" prompt="$2" default="$3" _tui_input=""
    printf '%s [%s]: ' "$prompt" "$default"
    if ! IFS= read -r _tui_input; then
        echo "[ERROR] Installer configuration input ended before completion." >&2
        exit 2
    fi
    printf -v "$variable" '%s' "${_tui_input:-$default}"
}

_tui_choice() {
    local variable="$1" prompt="$2" default="$3" allowed="$4" answer=""
    while true; do
        _tui_read answer "$prompt" "$default"
        case " $allowed " in
            *" $answer "*) printf -v "$variable" '%s' "$answer"; return 0 ;;
            *) echo "  Choose one of: ${allowed// /, }." >&2 ;;
        esac
    done
}

_tui_bool() {
    local variable="$1" prompt="$2" default="$3" answer=""
    while true; do
        _tui_read answer "$prompt (yes/no)" "$default"
        case "$answer" in
            y|Y|yes|Yes|YES) printf -v "$variable" '%s' "true"; return 0 ;;
            n|N|no|No|NO) printf -v "$variable" '%s' "false"; return 0 ;;
            *) echo "  Enter yes or no." >&2 ;;
        esac
    done
}

_run_configuration_tui() {
    if [[ "$target" == *.ps1 ]]; then
        echo "[ERROR] --tui is available from Linux, WSL2, and macOS shells." >&2
        echo "        Use .\\install.ps1 for the native Windows installer." >&2
        exit 2
    fi

    local mode tier profile install_dir lan offline bootstrap dry_run confirmed
    local voice workflows rag recommended hermes comfyui langfuse
    local -a install_args=(--non-interactive)

    echo "ODS Installer - configuration menu"
    echo "Review every installation default before any system change is made."
    echo ""
    echo "Runtime:  1) Local AI  2) Cloud APIs"
    _tui_choice mode "Runtime" "1" "1 2"

    tier="auto"
    if [[ "$mode" == "1" ]]; then
        echo "Tier:     auto, 1 (8GB), 2 (12GB), 3 (24GB), 4 (48GB+)"
        _tui_choice tier "Hardware tier" "auto" "auto 1 2 3 4"
    fi

    _tui_read install_dir "Install directory" "${INSTALL_DIR:-${ODS_HOME:-$HOME/ods}}"
    if [[ "$install_dir" != /* ]]; then
        echo "[ERROR] Install directory must be an absolute path." >&2
        exit 2
    fi

    echo "Profile:  1) Recommended  2) Minimal  3) Full  4) Custom"
    _tui_choice profile "Service profile" "1" "1 2 3 4"
    case "$profile" in
        1)
            voice=true; workflows=true; rag=true; recommended=true
            hermes=true; comfyui=true; langfuse=false
            ;;
        2)
            voice=false; workflows=false; rag=false; recommended=false
            hermes=false; comfyui=false; langfuse=false
            ;;
        3)
            voice=true; workflows=true; rag=true; recommended=true
            hermes=true; comfyui=true; langfuse=true
            ;;
        4)
            _tui_bool voice "Enable voice (Whisper + Kokoro)" "yes"
            _tui_bool workflows "Enable n8n workflows" "yes"
            _tui_bool rag "Enable RAG (Qdrant + embeddings)" "yes"
            _tui_bool recommended "Enable recommended support services" "yes"
            _tui_bool hermes "Enable Hermes Agent" "yes"
            _tui_bool comfyui "Enable ComfyUI image generation" "yes"
            _tui_bool langfuse "Enable Langfuse observability" "no"
            ;;
    esac

    _tui_bool lan "Expose web services to the LAN" "no"
    offline=false
    if [[ "$mode" == "1" ]]; then
        _tui_bool offline "Prepare for offline operation" "no"
    fi
    _tui_bool bootstrap "Use bootstrap fast-start" "yes"
    _tui_bool dry_run "Preview only (dry run)" "no"

    [[ "$mode" == "2" ]] && install_args+=(--cloud)
    [[ "$tier" != "auto" ]] && install_args+=(--tier "$tier")
    [[ "$profile" == "3" ]] && install_args+=(--all)
    if [[ "$profile" != "3" ]]; then
        [[ "$voice" == "true" ]] && install_args+=(--voice) || install_args+=(--no-voice)
        [[ "$workflows" == "true" ]] && install_args+=(--workflows) || install_args+=(--no-workflows)
        [[ "$rag" == "true" ]] && install_args+=(--rag) || install_args+=(--no-rag)
        [[ "$recommended" == "true" ]] && install_args+=(--recommended) || install_args+=(--no-recommended)
        [[ "$hermes" == "true" ]] && install_args+=(--hermes) || install_args+=(--no-hermes)
        [[ "$comfyui" == "true" ]] && install_args+=(--comfyui) || install_args+=(--no-comfyui)
        [[ "$langfuse" == "true" ]] && install_args+=(--langfuse) || install_args+=(--no-langfuse)
    fi
    [[ "$lan" == "true" ]] && install_args+=(--lan)
    [[ "$offline" == "true" ]] && install_args+=(--offline)
    [[ "$bootstrap" == "false" ]] && install_args+=(--no-bootstrap)
    [[ "$dry_run" == "true" ]] && install_args+=(--dry-run)

    echo ""
    echo "Installation plan"
    echo "  runtime:   $([[ "$mode" == "2" ]] && echo cloud || echo local)"
    echo "  tier:      $tier"
    echo "  directory: $install_dir"
    echo "  profile:   $profile"
    echo "  command:   install.sh ${install_args[*]}"
    echo ""
    _tui_bool confirmed "Start with this plan" "yes"
    if [[ "$confirmed" != "true" ]]; then
        echo "Installation cancelled; no changes were made."
        exit 0
    fi

    export INSTALL_DIR="$install_dir"
    set -- "${install_args[@]}"
    exec bash "$target" "$@"
}

if [[ "${1:-}" == "--tui" ]]; then
    if [[ $# -ne 1 ]]; then
        echo "[ERROR] --tui cannot be combined with installer flags; select them in the menu." >&2
        exit 2
    fi
    _run_configuration_tui
fi

if [[ ( "${1:-}" == "--help" || "${1:-}" == "-h" ) && "$target" == *.sh ]]; then
    # Buffer the delegated help before writing it. Consumers commonly probe this
    # command with `install.sh --help | grep -q ...`; writing a prefix before the
    # delegate can make grep close the pipe while install-core.sh is still
    # rendering help, turning a successful help request into SIGPIPE under
    # pipefail. Explicitly preserve the delegate's status even if that consumer
    # stops reading early.
    help_output=""
    help_status=0
    help_output="$(bash "$target" "$@" 2>&1)" || help_status=$?
    printf '%s\n' "$help_output" || true
    printf '%s\n' "Dispatcher option: --tui  Review and confirm all Unix installer defaults up front." || true
    exit "$help_status"
fi

case "$target" in
    unsupported:unknown)
        echo "[ERROR] Unsupported OS for this installer entrypoint."
        echo "        See docs/SUPPORT-MATRIX.md for supported platforms."
        exit 1
        ;;
    *)
        if [[ ! -f "$target" ]]; then
            echo "[ERROR] Installer target not found: $target"
            exit 1
        fi
        case "$target" in
            *.ps1)
                echo "[INFO] Windows installer target: $target"
                if command -v pwsh >/dev/null 2>&1; then
                    exec pwsh -File "$target" "$@"
                else
                    echo "[ERROR] PowerShell (pwsh) not found in this shell."
                    echo "        Run this from Windows PowerShell instead:"
                    echo "        .\\install.ps1"
                    exit 1
                fi
                ;;
            *)
                exec bash "$target" "$@"
                ;;
        esac
        ;;
esac
