#!/bin/bash
# ============================================================================
# ODS Installer — Phase 08: Pull Docker Images
# ============================================================================
# Part of: installers/phases/
# Purpose: Build image pull list and download all Docker images
#
# Expects: DRY_RUN, GPU_BACKEND, ENABLE_VOICE, ENABLE_WORKFLOWS,
#           ENABLE_RAG, ENABLE_QDRANT, ENABLE_EMBEDDINGS, ENABLE_HERMES,
#           DOCKER_CMD, LOG_FILE, BGRN, AMB, NC,
#           show_phase(), bootline(), signal(), ai(), ai_ok(), ai_warn(),
#           pull_with_progress()
# Provides: (Docker images pulled locally)
#
# Modder notes:
#   Add new container images or change image tags here.
# ============================================================================

# Isolated phase reuse (tests) gets the route predicate installers/lib/
# native-llm.sh gives install-core: a host-native llama-server is in use.
declare -F ods_native_llm_requested >/dev/null 2>&1 \
    || ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }

ods_progress 48 "images" "Downloading container images"
if [[ "${DRY_RUN:-false}" != true ]] && ods_native_llm_requested; then
    [[ -n "${COMPOSE_FLAGS:-}" ]] || {
        ai_bad "Host-native llama-server Compose selection is unavailable before image pulls."
        exit 1
    }
    read -ra _host_native_compose_flags <<< "$COMPOSE_FLAGS"
    if ! ods_host_native_assert_no_managed_llama_before_pixel_identity "${_host_native_compose_flags[@]}" \
        2>>"$LOG_FILE"; then
        ai_bad "Host-native llama-server Compose validation failed before image pulls; inspect $LOG_FILE."
        exit 1
    fi
    unset _host_native_compose_flags
fi
if [[ "${ODS_GATEWAY_ONLY:-false}" == true && "${DRY_RUN:-false}" != true ]]; then
    # Compose merges profile lists from overlays. A caller's inherited
    # COMPOSE_PROFILES=local-inference can therefore re-enable a managed model
    # even though the external route normally profiles it out. Fail before
    # pulling an unnecessary image; Phase 11 rechecks before container launch.
    [[ -n "${COMPOSE_FLAGS:-}" ]] || {
        ai_bad "Gateway-only Compose selection is unavailable before image pulls."
        exit 1
    }
    read -ra _gateway_compose_flags <<< "$COMPOSE_FLAGS"
    if ! ods_gateway_assert_no_managed_inference "${_gateway_compose_flags[@]}" \
        2>>"$LOG_FILE"; then
        ai_bad "Gateway-only Compose could start ODS-managed inference; inspect $LOG_FILE and clear COMPOSE_PROFILES."
        exit 1
    fi
    unset _gateway_compose_flags
fi
if [[ "${ENABLE_OPEN_WEBUI:-true}" != true && "${DRY_RUN:-false}" != true ]]; then
    [[ -n "${COMPOSE_FLAGS:-}" ]] || {
        ai_bad "No-WebUI Compose selection is unavailable before image pulls."
        exit 1
    }
    read -ra _no_webui_compose_flags <<< "$COMPOSE_FLAGS"
    if ! ods_compose_assert_no_webui_before_pixel_identity "${_no_webui_compose_flags[@]}" 2>>"$LOG_FILE"; then
        ai_bad "No-WebUI Compose could start Open WebUI; inspect $LOG_FILE and clear COMPOSE_PROFILES."
        exit 1
    fi
    unset _no_webui_compose_flags
fi
if [[ "$GPU_BACKEND" == "nvidia" && "${ENABLE_COMFYUI:-}" == "true" ]]; then
    show_phase 4 6 "Downloading Modules" "~5-10 min + ~30 min ComfyUI build"
else
    show_phase 4 6 "Downloading Modules" "~5-10 minutes"
fi

# Build image list with cinematic labels
# Format: "image|friendly_name"
PULL_LIST=()
if [[ "${ODS_MODE:-local}" != "cloud" && -z "${EXTERNAL_LLM_URL:-}" ]] && ! ods_native_llm_requested; then
    # Cloud, external and host-native routes do not run in-stack inference.
    # In WSL the Linux capability probe can report CPU while Windows owns the
    # model, so selecting this image from GPU_BACKEND alone wastes time and
    # disk.
    if [[ "$GPU_BACKEND" == "amd" ]]; then
        # The AMD overlays pin their images and ignore LLAMA_SERVER_IMAGE,
        # which a model profile can set to another backend's image.
        if [[ "${AMD_INFERENCE_BACKEND:-vulkan}" == "rocm" ]]; then
            PULL_LIST+=("ghcr.io/ggml-org/llama.cpp:server-rocm-b9014@sha256:68403f82fe496302bb1c681bab2ee569a04cc13da8fcf14456376b485562236d|LLAMA-SERVER — downloading the brain (AMD ROCm, about 7 GB)")
        else
            PULL_LIST+=("ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014@sha256:15c30b560d61ead1e08bee837503203a776fd968736118e313240c32157fd973|LLAMA-SERVER — downloading the brain (AMD Vulkan)")
        fi
    elif [[ "$GPU_BACKEND" == "intel" ]]; then
        PULL_LIST+=("${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-intel-b9014@sha256:9c7bbaad3663523a3deb8927d3cfbf58d33f00a7634c69843e9eeeda01568c1b}|LLAMA-SERVER — downloading the brain (Intel)")
    elif [[ "$GPU_BACKEND" == "sycl" ]]; then
        # The Arc overlay builds ods-llama-sycl:local; there is no remote image to pre-pull.
        :
    elif [[ "$GPU_BACKEND" == "cpu" ]]; then
        PULL_LIST+=("${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-b9014@sha256:2e7953dfef88f302bf0683bffa7dc1f8d86ef75910380bc41126ec5b8bedaf53}|LLAMA-SERVER — downloading the brain (CPU)")
    else
        PULL_LIST+=("${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f}|LLAMA-SERVER — downloading the brain (NVIDIA CUDA)")
    fi
fi
[[ "$GPU_BACKEND" == "amd" && "${ENABLE_COMFYUI:-false}" == "true" ]] && PULL_LIST+=("ignatberesnev/comfyui-gfx1151:v0.2@sha256:a38260b56a94fdf5aa9f951a96a73ef1987b70ebcbd4757447708a756a67abc0|COMFYUI — image generation engine (gfx1151)")
[[ "${ENABLE_OPEN_WEBUI:-true}" != "true" ]] || PULL_LIST+=("ghcr.io/open-webui/open-webui:v0.11.4@sha256:9591b13f13843c7721c2b8eaf7382846c81b3ffe126526d1888d1fed50c6a33f|OPEN WEBUI — interface module")
[[ "${ENABLE_PERPLEXICA:-false}" == "true" ]] && PULL_LIST+=("itzcrazykns1337/vane:v1.12.2@sha256:61f2bbf3386ff3df08911fb3de0e1893b04702a4d49ef13fbadbda937b47ab7c|PERPLEXICA — deep research engine")
if [[ "$ENABLE_VOICE" == "true" ]]; then
    if [[ "$GPU_BACKEND" == "nvidia" && "${WHISPER_ACCELERATION:-cuda}" == "cuda" ]]; then
        PULL_LIST+=("ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda@sha256:f4eb14d1c53b19c5bd1f76d10ea9fc10288ceaf82dca09506d3f0ef92ee943de|WHISPER — ears online (Speaches STT, CUDA)")
    else
        PULL_LIST+=("${WHISPER_IMAGE:-ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu@sha256:2163775b6df5e451a71200e8f675fed68dbd8ab184fc604453d549e486f22fd2}|WHISPER — ears online (Speaches STT, CPU)")
    fi
    PULL_LIST+=("ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.4@sha256:c8812546d358cbfd6a5c4087a28795b2b001d8e32d7a322eedd246e6bc13cb55|KOKORO — voice module")
fi
[[ "$ENABLE_WORKFLOWS" == "true" ]] && PULL_LIST+=("n8nio/n8n:2.41.6@sha256:87e0bab2c93192e8dd885ff7b0697c22a1bd97489568a8c67cc140fd7dbb342d|N8N — automation engine")
[[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && PULL_LIST+=("qdrant/qdrant:v1.16.3@sha256:0425e3e03e7fd9b3dc95c4214546afe19de2eb2e28ca621441a56663ac6e1f46|QDRANT — memory vault")
if [[ "$ENABLE_HERMES" == "true" ]]; then
    # Version-pinned upstream image. See extensions/services/hermes/compose.yaml
    # and docs/HERMES.md for the bump process. Hermes-proxy is the auth gate
    # (Caddy) and is pulled alongside Hermes.
    PULL_LIST+=("${HERMES_AGENT_IMAGE:-nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7}|HERMES — default agent (Nous Research)")
    PULL_LIST+=("caddy:2.11.3-alpine@sha256:86deaf5e3d3408a6ccec08fbb79989783dd26e206ae10bcf78a801dc8c9ab794|HERMES PROXY — magic-link auth gate (Caddy)")
fi
[[ "${ENABLE_EMBEDDINGS:-${ENABLE_RAG:-false}}" == "true" ]] && PULL_LIST+=("ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.1@sha256:b7772cdd9dcbced147b16a7dff17d4aed1ab36333f8d3e686c50d2175e1d2126|TEI — embedding engine")

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
    if [[ "${ODS_MODE:-local}" != "cloud" && "$GPU_BACKEND" == "amd" ]]; then
        # Check the pinned AMD image before downloading. There is no fallback:
        # the AMD overlays run only their own pinned image.
        for _entry in "${PULL_LIST[@]}"; do
            [[ "${_entry##*|}" == LLAMA-SERVER* ]] || continue
            ai "Validating llama-server image tag before download..."
            if docker_image_available "${_entry%%|*}"; then
                ai_ok "llama-server image available: ${_entry%%|*}"
            else
                ai_bad "llama-server image is unavailable: ${_entry%%|*}"
                ai "Docker cannot resolve the pinned AMD llama.cpp image. Check registry access and the Docker daemon network."
                exit 1
            fi
        done
        unset _entry
    fi
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
