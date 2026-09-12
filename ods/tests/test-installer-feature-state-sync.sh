#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"

run_case() {
    local selected="$1" source_state="$2"
    local test_root source_root install_root
    test_root="$(mktemp -d)"
    source_root="$test_root/source"
    install_root="$test_root/install"
    trap 'rm -rf -- "$test_root"' RETURN

    mkdir -p "$source_root/extensions/services/openclaw" \
        "$install_root/extensions/services/openclaw"
    printf 'services: {}\n' \
        >"$source_root/extensions/services/openclaw/compose.yaml${source_state}"

    # Reproduce an interrupted/non-pruning upgrade with both the old enabled
    # file and the newly copied disabled state present in the install tree.
    printf 'services: {}\n' \
        >"$install_root/extensions/services/openclaw/compose.yaml"
    printf 'services: {}\n' \
        >"$install_root/extensions/services/openclaw/compose.yaml.disabled"

    (
        INTERACTIVE=false
        DRY_RUN=false
        INSTALL_CHOICE=1
        TIER=1
        ODS_MODE=local
        ENABLE_VOICE=false
        ENABLE_WORKFLOWS=false
        ENABLE_RAG=false
        ENABLE_HERMES=false
        ENABLE_OPENCLAW="$selected"
        ENABLE_OPENCODE=false
        ENABLE_COMFYUI=false
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
        GPU_BACKEND=cpu
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
    done
}

run_case false ""
run_case true ".disabled"

echo "PASS: feature selection reconciles source and installed compose states"
