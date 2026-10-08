#!/bin/bash
# ODS Installer entrypoint (PR-1 dispatcher)
# Pass-through options (implemented in install-core.sh):
# --dry-run --skip-docker --force --tier --voice --workflows --rag
# --hermes --pixel --all --non-interactive --no-bootstrap --bootstrap --offline
# --native-llm-url --native-llm-host-transport --native-llm-model
# --native-llm-context-size --native-llm-api-key-env --native-llm-gpu-name
# --native-llm-gpu-vram-mb
#   (the retired --use-existing-lemonade/--lemonade-* flags map onto these or
#   onto --external-llm-* for one release)
# --preflight-only (environment checks only; used by get-ods.sh --force through
#   installers/reinstall-preflight.sh before an existing install is removed)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/installers/dispatch.sh"

target="$(resolve_installer_target)"

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
