#!/bin/bash
# ============================================================================
# ODS Installer — Phase 08: Pull Docker Images
# ============================================================================
# Part of: installers/phases/
# Purpose: Build image pull list and download all Docker images
#
# Expects: DRY_RUN, GPU_BACKEND, ENABLE_VOICE, ENABLE_WORKFLOWS,
#           ENABLE_RAG, ENABLE_QDRANT, ENABLE_EMBEDDINGS, ENABLE_HERMES, ENABLE_OPENCLAW,
#           DOCKER_CMD, LOG_FILE, BGRN, AMB, NC,
#           show_phase(), bootline(), signal(), ai(), ai_ok(), ai_warn(),
#           pull_with_progress()
# Provides: (Docker images pulled locally)
#
# Modder notes:
#   Add new container images or change image tags here.
# ============================================================================

ods_progress 48 "images" "Downloading container images"
if [[ "$GPU_BACKEND" == "nvidia" && "${ENABLE_COMFYUI:-}" == "true" ]]; then
    show_phase 4 6 "Downloading Modules" "~5-10 min + ~30 min ComfyUI build"
else
    show_phase 4 6 "Downloading Modules" "~5-10 minutes"
fi

# Build image list with cinematic labels
# Format: "image|friendly_name"
PULL_LIST=()
if [[ "$GPU_BACKEND" == "amd" ]]; then
    case "${LEMONADE_EXTERNAL:-false}" in
        true|TRUE|1|yes|YES|on|ON) _lemonade_external=true ;;
        *) _lemonade_external=false ;;
    esac
    if [[ "$_lemonade_external" != "true" ]]; then
        _lemonade_image="${LEMONADE_SERVER_IMAGE:-${BACKEND_LEMONADE_CONTAINER_IMAGE:-ghcr.io/lemonade-sdk/lemonade-server:v10.2.0}}"
        PULL_LIST+=("${_lemonade_image}|LEMONADE — downloading the brain (AMD ROCm)")
    fi
    [[ "$ENABLE_COMFYUI" == "true" ]] && PULL_LIST+=("ignatberesnev/comfyui-gfx1151:v0.2|COMFYUI — image generation engine (gfx1151)")
elif [[ "$GPU_BACKEND" == "cpu" ]]; then
    PULL_LIST+=("${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-b8248}|LLAMA-SERVER — downloading the brain (CPU)")
else
    PULL_LIST+=("${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda-b9014}|LLAMA-SERVER — downloading the brain (NVIDIA CUDA)")
fi
PULL_LIST+=("ghcr.io/open-webui/open-webui:v0.7.2|OPEN WEBUI — interface module")
PULL_LIST+=("itzcrazykns1337/perplexica:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e|PERPLEXICA — deep research engine")
if [[ "$ENABLE_VOICE" == "true" ]]; then
    if [[ "$GPU_BACKEND" == "nvidia" ]]; then
        PULL_LIST+=("ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda|WHISPER — ears online (Speaches STT, CUDA)")
    else
        PULL_LIST+=("ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu|WHISPER — ears online (Speaches STT)")
    fi
    PULL_LIST+=("ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.4|KOKORO — voice module")
fi
[[ "$ENABLE_WORKFLOWS" == "true" ]] && PULL_LIST+=("n8nio/n8n:2.6.4|N8N — automation engine")
[[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && PULL_LIST+=("qdrant/qdrant:v1.16.3|QDRANT — memory vault")
if [[ "$ENABLE_HERMES" == "true" ]]; then
    # Version-pinned upstream image. See extensions/services/hermes/compose.yaml
    # and docs/HERMES.md for the bump process. Hermes-proxy is the auth gate
    # (Caddy) and is pulled alongside Hermes.
    PULL_LIST+=("${HERMES_AGENT_IMAGE:-nousresearch/hermes-agent:v2026.5.16}|HERMES — default agent (Nous Research)")
    PULL_LIST+=("caddy:2.11.3-alpine|HERMES PROXY — magic-link auth gate (Caddy)")
fi
[[ "$ENABLE_OPENCLAW" == "true" ]] && PULL_LIST+=("ghcr.io/openclaw/openclaw:2026.3.8|OPENCLAW — agent framework")
[[ "${ENABLE_EMBEDDINGS:-${ENABLE_RAG:-false}}" == "true" ]] && PULL_LIST+=("ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1|TEI — embedding engine")

if command -v ods_compose_external_images >/dev/null 2>&1 && [[ -n "${COMPOSE_FLAGS:-}" ]]; then
    read -ra _phase08_compose_flags_arr <<< "$COMPOSE_FLAGS"
    _phase08_compose_images=()
    _phase08_compose_image_output=""
    if _phase08_compose_image_output="$(ods_compose_external_images "${DOCKER_COMPOSE_CMD:-docker compose}" "${_phase08_compose_flags_arr[@]}" 2>>"$LOG_FILE")"; then
        if [[ -n "$_phase08_compose_image_output" ]]; then
            mapfile -t _phase08_compose_images <<< "$_phase08_compose_image_output"
        fi
        for _compose_img in "${_phase08_compose_images[@]}"; do
            _already_listed=false
            for _entry in "${PULL_LIST[@]}"; do
                if [[ "${_entry%%|*}" == "$_compose_img" ]]; then
                    _already_listed=true
                    break
                fi
            done
            if [[ "$_already_listed" != "true" ]]; then
                PULL_LIST+=("$_compose_img|COMPOSE — ${_compose_img}")
            fi
        done
    else
        ai_warn "Could not audit Docker Compose image list during Phase 4; Phase 5 will re-check before launch."
    fi
fi

if $DRY_RUN; then
    ai "[DRY RUN] I would download ${#PULL_LIST[@]} modules."
else
    if [[ "${ODS_MODE:-local}" != "cloud" && ( "$GPU_BACKEND" == "nvidia" || "$GPU_BACKEND" == "cpu" || "$GPU_BACKEND" == "intel" || "$GPU_BACKEND" == "sycl" ) ]]; then
        _llama_image=""
        _llama_label=""
        _llama_index=-1
        for _idx in "${!PULL_LIST[@]}"; do
            entry="${PULL_LIST[$_idx]}"
            _entry_img="${entry%%|*}"
            _entry_label="${entry##*|}"
            if [[ "$_entry_label" == LLAMA-SERVER* ]]; then
                _llama_image="$_entry_img"
                _llama_label="$_entry_label"
                _llama_index="$_idx"
                break
            fi
        done

        if [[ -n "$_llama_image" ]]; then
            ai "Validating llama-server image tag before download..."
            if [[ -z "${LLAMA_SERVER_IMAGE_FALLBACK:-}" && -f "$INSTALL_DIR/.env" ]]; then
                _llama_fallback_from_env="$(sed -n 's/^LLAMA_SERVER_IMAGE_FALLBACK=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -n 1 || true)"
                _llama_fallback_from_env="${_llama_fallback_from_env#\"}"
                _llama_fallback_from_env="${_llama_fallback_from_env%\"}"
                _llama_fallback_from_env="${_llama_fallback_from_env#\'}"
                _llama_fallback_from_env="${_llama_fallback_from_env%\'}"
                [[ -n "$_llama_fallback_from_env" ]] && LLAMA_SERVER_IMAGE_FALLBACK="$_llama_fallback_from_env"
            fi
            _validated_llama_image=""
            if ! validate_docker_image_or_fallback _validated_llama_image "$_llama_image" "llama-server" "LLAMA_SERVER_IMAGE_FALLBACK"; then
                exit 1
            fi
            if [[ "$_validated_llama_image" != "$_llama_image" ]]; then
                LLAMA_SERVER_IMAGE="$_validated_llama_image"
                PULL_LIST[$_llama_index]="${_validated_llama_image}|${_llama_label}"
                if [[ -f "$INSTALL_DIR/.env" ]]; then
                    if grep -q '^LLAMA_SERVER_IMAGE=' "$INSTALL_DIR/.env"; then
                        sed -i.bak "s|^LLAMA_SERVER_IMAGE=.*|LLAMA_SERVER_IMAGE=${_validated_llama_image}|" "$INSTALL_DIR/.env" && rm -f "$INSTALL_DIR/.env.bak"
                    else
                        printf '\nLLAMA_SERVER_IMAGE=%s\n' "$_validated_llama_image" >> "$INSTALL_DIR/.env"
                    fi
                fi
            fi
        fi
    fi

    if [[ "${ENABLE_HERMES:-false}" == "true" ]]; then
        _hermes_image=""
        _hermes_label=""
        _hermes_index=-1
        for _idx in "${!PULL_LIST[@]}"; do
            entry="${PULL_LIST[$_idx]}"
            _entry_img="${entry%%|*}"
            _entry_label="${entry##*|}"
            if [[ "$_entry_label" == HERMES\ * ]]; then
                _hermes_image="$_entry_img"
                _hermes_label="$_entry_label"
                _hermes_index="$_idx"
                break
            fi
        done

        if [[ -n "$_hermes_image" ]]; then
            ai "Validating Hermes Agent image tag before download..."
            if [[ -z "${HERMES_AGENT_IMAGE_FALLBACK:-}" && -f "$INSTALL_DIR/.env" ]]; then
                _hermes_fallback_from_env="$(sed -n 's/^HERMES_AGENT_IMAGE_FALLBACK=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -n 1 || true)"
                _hermes_fallback_from_env="${_hermes_fallback_from_env#\"}"
                _hermes_fallback_from_env="${_hermes_fallback_from_env%\"}"
                _hermes_fallback_from_env="${_hermes_fallback_from_env#\'}"
                _hermes_fallback_from_env="${_hermes_fallback_from_env%\'}"
                [[ -n "$_hermes_fallback_from_env" ]] && HERMES_AGENT_IMAGE_FALLBACK="$_hermes_fallback_from_env"
            fi
            _validated_hermes_image=""
            if ! validate_docker_image_or_fallback _validated_hermes_image "$_hermes_image" "Hermes Agent" "HERMES_AGENT_IMAGE_FALLBACK" "HERMES_AGENT_IMAGE"; then
                exit 1
            fi
            if [[ "$_validated_hermes_image" != "$_hermes_image" ]]; then
                HERMES_AGENT_IMAGE="$_validated_hermes_image"
                PULL_LIST[$_hermes_index]="${_validated_hermes_image}|${_hermes_label}"
                if [[ -f "$INSTALL_DIR/.env" ]]; then
                    if grep -q '^HERMES_AGENT_IMAGE=' "$INSTALL_DIR/.env"; then
                        sed -i.bak "s|^HERMES_AGENT_IMAGE=.*|HERMES_AGENT_IMAGE=${_validated_hermes_image}|" "$INSTALL_DIR/.env" && rm -f "$INSTALL_DIR/.env.bak"
                    else
                        printf '\nHERMES_AGENT_IMAGE=%s\n' "$_validated_hermes_image" >> "$INSTALL_DIR/.env"
                    fi
                fi
            fi
        fi
    fi

    echo ""
    bootline
    echo -e "${BGRN}DOWNLOAD SEQUENCE${NC}"
    echo -e "${AMB}This is the long scene.${NC} (largest module first)"
    bootline
    echo ""
    signal "Take a break for ten minutes. I've got this."
    echo ""

    pull_count=0
    pull_total=${#PULL_LIST[@]}
    pull_failed=0

    for entry in "${PULL_LIST[@]}"; do
        img="${entry%%|*}"
        label="${entry##*|}"
        pull_count=$((pull_count + 1))

        # Sub-milestone: interpolate progress 48-64% across image pulls
        _img_pct=$(( 48 + (pull_count - 1) * 16 / pull_total ))
        ods_progress "$_img_pct" "images" "Pulling image $pull_count/$pull_total"

        if ! pull_with_progress "$img" "$label" "$pull_count" "$pull_total"; then
            ai_warn "Failed to pull $img — retry after fixing Docker registry/network/disk access"
            ai "  If this persists, check your network connection and disk space"
            pull_failed=$((pull_failed + 1))
        fi
    done

    echo ""
    if [[ $pull_failed -eq 0 ]]; then
        ai_ok "All $pull_total modules downloaded"
    else
        ai_bad "$pull_failed of $pull_total modules failed to download"
        ai "Phase 5 will not perform unprotected Docker pulls during compose up."
        ai "Fix the registry/network/disk error above, then re-run ./install.sh to resume."
        exit 1
    fi
fi
