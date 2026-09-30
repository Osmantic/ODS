#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"

run_case() {
    local selected="$1" source_state="$2" comfyui_requested="${3:-false}"
    local gpu_backend="${4:-cpu}" comfyui_expected="${5:-false}"
    local test_root source_root install_root
    test_root="$(mktemp -d)"
    source_root="$test_root/source"
    install_root="$test_root/install"
    trap 'rm -rf -- "$test_root"' RETURN

    mkdir -p "$source_root/extensions/services/openclaw" \
        "$install_root/extensions/services/openclaw" \
        "$source_root/extensions/services/comfyui" \
        "$install_root/extensions/services/comfyui"
    printf 'services: {}\n' \
        >"$source_root/extensions/services/openclaw/compose.yaml${source_state}"

    # Reproduce an interrupted/non-pruning upgrade with both the old enabled
    # file and the newly copied disabled state present in the install tree.
    printf 'services: {}\n' \
        >"$install_root/extensions/services/openclaw/compose.yaml"
    printf 'services: {}\n' \
        >"$install_root/extensions/services/openclaw/compose.yaml.disabled"
    # An upgrade may retain an enabled ComfyUI fragment even though the
    # current WSL backend exposes no Docker GPU. Selection must reconcile it.
    printf 'services: {}\n' >"$source_root/extensions/services/comfyui/compose.yaml"
    printf 'services: {}\n' >"$install_root/extensions/services/comfyui/compose.yaml"

    (
        INTERACTIVE=false
        DRY_RUN=false
        INSTALL_CHOICE=1
        TIER=4
        ODS_MODE=local
        ENABLE_VOICE=false
        ENABLE_WORKFLOWS=false
        ENABLE_RAG=false
        ENABLE_HERMES=false
        ENABLE_OPENCLAW="$selected"
        ENABLE_OPENCODE=false
        ENABLE_COMFYUI="$comfyui_requested"
        ENABLE_LANGFUSE=false
        ENABLE_RECOMMENDED=false
        ENABLE_PIXEL_RUNTIME=false
        ENABLE_APE=false
        ENABLE_PERPLEXICA=false
        ENABLE_PRIVACY_SHIELD=false
        ENABLE_ODS_PROXY=false
        ENABLE_TAILSCALE=false
        ENABLE_BRAVE_SEARCH=false
        GPU_COUNT=1
        GPU_BACKEND="$gpu_backend"
        HOST_ARCH=x86_64
        HOST_PAGE_SIZE=4096
        SCRIPT_DIR="$source_root"
        INSTALL_DIR="$install_root"
        MAX_CONTEXT=4096
        LLM_MODEL_SIZE_MB=0

        ods_progress() { :; }
        ai_warn() { :; }
        log() { :; }
        warn() { :; }
        success() { :; }
        chapter() { :; }
        bootline() { :; }
        signal() { :; }
        show_phase() { :; }
        show_install_menu() { :; }

        # shellcheck source=/dev/null
        source "$FEATURES_PHASE" >/dev/null
        printf '%s\n' "$ENABLE_COMFYUI" >"$test_root/comfyui-selection"
    )

    local expected_suffix unexpected_suffix
    if [[ "$selected" == "true" ]]; then
        expected_suffix=""
        unexpected_suffix=".disabled"
    else
        expected_suffix=".disabled"
        unexpected_suffix=""
    fi

    for root in "$source_root" "$install_root"; do
        test -f "$root/extensions/services/openclaw/compose.yaml${expected_suffix}"
        test ! -e "$root/extensions/services/openclaw/compose.yaml${unexpected_suffix}"
        if [[ "$comfyui_expected" == true ]]; then
            test -f "$root/extensions/services/comfyui/compose.yaml"
            test ! -e "$root/extensions/services/comfyui/compose.yaml.disabled"
        else
            test ! -e "$root/extensions/services/comfyui/compose.yaml"
            test -f "$root/extensions/services/comfyui/compose.yaml.disabled"
        fi
    done
    [[ "$(cat "$test_root/comfyui-selection")" == "$comfyui_expected" ]]
}

run_case false ""
run_case true ".disabled"
run_case false "" true cpu false
run_case false "" true amd true
run_case false "" true nvidia true

echo "PASS: feature selection reconciles source and installed compose states"
