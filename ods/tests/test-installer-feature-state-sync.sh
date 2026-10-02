#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"
source "$ROOT_DIR/installers/lib/installed-feature-state.sh"

run_case() {
    local selected="$1" source_state="$2" comfyui_requested="${3:-false}"
    local gpu_backend="${4:-cpu}" comfyui_expected="${5:-false}"
    local brave_requested="${6:-false}" brave_key_mode="${7:-none}"
    local hermes_selected="${8:-false}" proxy_selected="${9:-false}"
    local expect_invalid="${10:-false}"
    local whisper_selected="${11:-false}" tts_selected="${12:-false}"
    local test_root source_root install_root
    test_root="$(mktemp -d)"
    source_root="$test_root/source"
    install_root="$test_root/install"
    trap 'rm -rf -- "$test_root"' RETURN

    mkdir -p "$source_root/extensions/services/openclaw" \
        "$install_root/extensions/services/openclaw" \
        "$source_root/extensions/services/whisper" \
        "$install_root/extensions/services/whisper" \
        "$source_root/extensions/services/tts" \
        "$install_root/extensions/services/tts" \
        "$source_root/extensions/services/comfyui" \
        "$install_root/extensions/services/comfyui" \
        "$source_root/extensions/services/brave-search" \
        "$install_root/extensions/services/brave-search" \
        "$source_root/extensions/services/hermes" \
        "$install_root/extensions/services/hermes" \
        "$source_root/extensions/services/hermes-proxy" \
        "$install_root/extensions/services/hermes-proxy"
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
    printf 'services: {}\n' >"$source_root/extensions/services/hermes/compose.yaml"
    printf 'services: {}\n' >"$source_root/extensions/services/hermes-proxy/compose.yaml"
    for service in whisper tts; do
        printf 'services: {}\n' >"$source_root/extensions/services/$service/compose.yaml"
        local selected_voice="$whisper_selected"
        [[ "$service" == tts ]] && selected_voice="$tts_selected"
        if [[ "$selected_voice" == true ]]; then
            printf 'services: {}\n' >"$install_root/extensions/services/$service/compose.yaml"
        else
            printf 'services: {}\n' >"$install_root/extensions/services/$service/compose.yaml.disabled"
        fi
    done
    if [[ "$hermes_selected" == true ]]; then
        printf 'services: {}\n' >"$install_root/extensions/services/hermes/compose.yaml"
    else
        printf 'services: {}\n' >"$install_root/extensions/services/hermes/compose.yaml.disabled"
    fi
    if [[ "$proxy_selected" == true ]]; then
        printf 'services: {}\n' >"$install_root/extensions/services/hermes-proxy/compose.yaml"
    else
        printf 'services: {}\n' >"$install_root/extensions/services/hermes-proxy/compose.yaml.disabled"
    fi
    if [[ "$hermes_selected" == true || "$proxy_selected" == true ||
          "$whisper_selected" == true || "$tts_selected" == true ]]; then
        printf 'ODS_MODE=local\n' >"$install_root/.env"
    fi
    if [[ "$brave_key_mode" == file || "$brave_key_mode" == empty-override ]]; then
        printf 'BRAVE_SEARCH_API_KEY="fixture-value"\n' >"$install_root/.env"
    fi

    (
        # This fixture is an unmanaged install, regardless of the developer's
        # real Pixel management marker.
        export HOME="$test_root/home"
        mkdir -p "$HOME"
        INTERACTIVE=false
        DRY_RUN=false
        INSTALL_CHOICE=1
        TIER=4
        ODS_MODE=local
        ENABLE_VOICE=false
        ENABLE_WORKFLOWS=false
        ENABLE_RAG=false
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
        ENABLE_WHISPER="$(ods_installed_service_default "$INSTALL_DIR" whisper false)"
        ENABLE_TTS="$(ods_installed_service_default "$INSTALL_DIR" tts false)"
        [[ "$ENABLE_WHISPER" == true || "$ENABLE_TTS" == true ]] && ENABLE_VOICE=true
        ENABLE_HERMES="$(ods_installed_service_default "$INSTALL_DIR" hermes false)"
        ENABLE_HERMES_PROXY="$(ods_installed_service_default "$INSTALL_DIR" hermes-proxy "$ENABLE_HERMES")"
        MAX_CONTEXT=4096
        LLM_MODEL_SIZE_MB=0

        ods_progress() { :; }
        ai_warn() { printf '%s\n' "$1" >"$test_root/warning"; }
        log() { :; }
        error() { printf '%s\n' "$1" >"$test_root/error"; }
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
        source "$(dirname "$FEATURES_PHASE")/../lib/installed-feature-state.sh"
        if [[ "$expect_invalid" == true ]]; then
            if source "$FEATURES_PHASE" >/dev/null; then
                exit 31
            fi
            exit 0
        fi
        source "$FEATURES_PHASE" >/dev/null
        printf '%s\n' "$ENABLE_COMFYUI" >"$test_root/comfyui-selection"
        printf '%s\n' "$ENABLE_BRAVE_SEARCH" >"$test_root/brave-selection"
    )

    if [[ "$expect_invalid" == true ]]; then
        grep -Fq 'Hermes proxy requires Hermes' "$test_root/error"
        test -f "$source_root/extensions/services/hermes/compose.yaml"
        test -f "$install_root/extensions/services/hermes/compose.yaml.disabled"
        test -f "$install_root/extensions/services/hermes-proxy/compose.yaml"
        return
    fi

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
        for service in hermes hermes-proxy; do
            local hermes_expected="$hermes_selected"
            [[ "$service" == hermes-proxy ]] && hermes_expected="$proxy_selected"
            if [[ "$hermes_expected" == true ]]; then
                test -f "$root/extensions/services/$service/compose.yaml"
                test ! -e "$root/extensions/services/$service/compose.yaml.disabled"
            else
                test ! -e "$root/extensions/services/$service/compose.yaml"
                test -f "$root/extensions/services/$service/compose.yaml.disabled"
            fi
        done
        for service in whisper tts; do
            local voice_expected="$whisper_selected"
            [[ "$service" == tts ]] && voice_expected="$tts_selected"
            if [[ "$voice_expected" == true ]]; then
                test -f "$root/extensions/services/$service/compose.yaml"
                test ! -e "$root/extensions/services/$service/compose.yaml.disabled"
            else
                test ! -e "$root/extensions/services/$service/compose.yaml"
                test -f "$root/extensions/services/$service/compose.yaml.disabled"
            fi
        done
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
run_case false "" false cpu false false none true false
run_case false "" false cpu false false none true true
run_case false "" false cpu false false none false true true
run_case false "" false cpu false false none false false false true false
run_case false "" false cpu false false none false false false false true

echo "PASS: feature selection reconciles source and installed compose states"
