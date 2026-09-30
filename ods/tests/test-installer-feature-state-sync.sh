#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"

run_case() {
    local selected="$1" source_state="$2" comfyui_requested="${3:-false}"
    local gpu_backend="${4:-cpu}" comfyui_expected="${5:-false}"
    local brave_requested="${6:-false}" brave_key_mode="${7:-none}"
    local test_root source_root install_root
    test_root="$(mktemp -d)"
    source_root="$test_root/source"
    install_root="$test_root/install"
    trap 'rm -rf -- "$test_root"' RETURN

    mkdir -p "$source_root/extensions/services/openclaw" \
        "$install_root/extensions/services/openclaw" \
        "$source_root/extensions/services/comfyui" \
        "$install_root/extensions/services/comfyui" \
        "$source_root/extensions/services/brave-search" \
        "$install_root/extensions/services/brave-search"
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
    printf 'services: {}\n' >"$source_root/extensions/services/brave-search/compose.yaml"
    printf 'services: {}\n' >"$install_root/extensions/services/brave-search/compose.yaml"
    if [[ "$brave_key_mode" == file || "$brave_key_mode" == empty-override ]]; then
        printf 'BRAVE_SEARCH_API_KEY="fixture-value"\n' >"$install_root/.env"
    fi

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
        ENABLE_PIXEL=false
        ENABLE_PIXEL_RUNTIME=false
        ENABLE_APE=false
        ENABLE_PERPLEXICA=false
        ENABLE_PRIVACY_SHIELD=false
        ENABLE_ODS_PROXY=false
        ENABLE_TAILSCALE=false
        ENABLE_BRAVE_SEARCH="$brave_requested"
        unset BRAVE_SEARCH_API_KEY
        if [[ "$brave_key_mode" == env ]]; then BRAVE_SEARCH_API_KEY=fixture-value; fi
        if [[ "$brave_key_mode" == empty-override ]]; then BRAVE_SEARCH_API_KEY=; fi
        GPU_COUNT=1
        GPU_BACKEND="$gpu_backend"
        HOST_ARCH=x86_64
        HOST_PAGE_SIZE=4096
        SCRIPT_DIR="$source_root"
        INSTALL_DIR="$install_root"
        MAX_CONTEXT=4096
        LLM_MODEL_SIZE_MB=0

        ods_progress() { :; }
        ai_warn() { printf '%s\n' "$1" >"$test_root/warning"; }
        log() { :; }
        warn() { :; }
        success() { :; }
        chapter() { :; }
        bootline() { :; }
        signal() { :; }
        show_phase() { :; }
        show_install_menu() { :; }

        # shellcheck source=../installers/lib/external-services.sh
        source "$ROOT_DIR/installers/lib/external-services.sh"
        # shellcheck source=/dev/null
        source "$FEATURES_PHASE" >/dev/null
        printf '%s\n' "$ENABLE_COMFYUI" >"$test_root/comfyui-selection"
        printf '%s\n' "$ENABLE_BRAVE_SEARCH" >"$test_root/brave-selection"
    )

    local expected_suffix unexpected_suffix brave_expected=false
    if [[ "$selected" == "true" ]]; then
        expected_suffix=""
        unexpected_suffix=".disabled"
    else
        expected_suffix=".disabled"
        unexpected_suffix=""
    fi

    if [[ "$brave_requested" == true &&
          ( "$brave_key_mode" == env || "$brave_key_mode" == file ) ]]; then
        brave_expected=true
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
        if [[ "$brave_expected" == true ]]; then
            test -f "$root/extensions/services/brave-search/compose.yaml"
            test ! -e "$root/extensions/services/brave-search/compose.yaml.disabled"
        else
            test ! -e "$root/extensions/services/brave-search/compose.yaml"
            test -f "$root/extensions/services/brave-search/compose.yaml.disabled"
        fi
    done
    [[ "$(cat "$test_root/comfyui-selection")" == "$comfyui_expected" ]]
    [[ "$(cat "$test_root/brave-selection")" == "$brave_expected" ]]
    if [[ "$brave_requested" == true && "$brave_expected" == false ]]; then
        grep -Fq 'Brave Search was skipped because BRAVE_SEARCH_API_KEY is missing' "$test_root/warning"
    fi
}

run_case false ""
run_case true ".disabled"
run_case false "" true cpu false
run_case false "" true amd true
run_case false "" true nvidia true
run_case false "" false cpu false true none
run_case false "" false cpu false true env
run_case false "" false cpu false true file
run_case false "" false cpu false true empty-override

echo "PASS: feature selection reconciles source and installed compose states"
