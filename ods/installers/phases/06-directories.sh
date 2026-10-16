#!/bin/bash
# ============================================================================
# ODS Installer — Phase 06: Directories & Configuration
# ============================================================================
# Part of: installers/phases/
# Purpose: Create directories, copy source files, generate .env, configure
#          SearXNG, and validate .env schema
#
# Expects: SCRIPT_DIR, INSTALL_DIR, LOG_FILE, DRY_RUN, INTERACTIVE,
#           TIER, TIER_NAME, VERSION, GPU_BACKEND, SYSTEM_TZ,
#           LLM_MODEL, MAX_CONTEXT, GGUF_FILE, COMPOSE_FLAGS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_HERMES,
#           GPU_ASSIGNMENT_JSON, COMFYUI_GPU_UUID, WHISPER_GPU_UUID,
#           EMBEDDINGS_GPU_UUID, LLAMA_SERVER_GPU_UUIDS, LLAMA_ARG_SPLIT_MODE,
#           LLAMA_ARG_TENSOR_SPLIT,
#           chapter(), ai(), ai_ok(), ai_warn(), log(), warn(), error()
# Provides: WEBUI_SECRET, N8N_PASS, LITELLM_KEY, LIVEKIT_SECRET,
#           DASHBOARD_API_KEY, SHIELD_API_KEY, TOKEN_SPY_API_KEY,
#           OPENCODE_SERVER_PASSWORD, GPU_ASSIGNMENT_JSON_B64 (in .env)
#           PIXEL_SOURCE_URL, PIXEL_SOURCE_REF, PIXEL_SOURCE_DIR when Pixel is enabled
#
# Modder notes:
#   This is the largest phase. Modify .env generation, add new config files,
#   or change directory layout here.
# ============================================================================

# shellcheck source=installers/lib/extensions-library-copy.sh
source "$SCRIPT_DIR/installers/lib/extensions-library-copy.sh"
# install-core sources these; isolated phase reuse (tests) may not.
declare -F ods_native_llm_normalize_origin >/dev/null 2>&1 \
    || source "$SCRIPT_DIR/installers/lib/native-llm.sh"
declare -F ods_amd_target_is_cdna >/dev/null 2>&1 \
    || source "$SCRIPT_DIR/installers/lib/amd-runtime.sh"

ods_progress 38 "directories" "Preparing installation directory"
chapter "SETTING UP INSTALLATION"

_phase06_step() {
    local step="$1"
    export INSTALL_PHASE="06-directories/${step}"
    log "Phase 06 step: ${step}"
}

_phase06_generate_hex_secret() {
    local bytes="$1" secret expected_length
    case "$bytes" in
        ''|*[!0-9]*|0)
            error "Secret byte count must be a positive integer: $bytes"
            return 1
            ;;
    esac

    if command -v openssl >/dev/null 2>&1; then
        secret="$(openssl rand -hex "$bytes")" || secret=""
    elif command -v xxd >/dev/null 2>&1; then
        secret="$(head -c "$bytes" /dev/urandom | xxd -p | tr -d '\n')" || secret=""
    elif command -v od >/dev/null 2>&1; then
        secret="$(od -An -N "$bytes" -tx1 /dev/urandom | tr -d ' \n')" || secret=""
    else
        error "Cannot generate installer secrets: install openssl, xxd, or od."
        return 1
    fi

    expected_length=$((bytes * 2))
    if [[ "${#secret}" -ne "$expected_length" || "$secret" == *[!0-9a-fA-F]* ]]; then
        error "Secret generator returned invalid output; refusing to write .env."
        return 1
    fi
    printf '%s' "$secret"
}

_phase06_env_hex_secret() {
    local key="$1" bytes="$2" prefix="${3:-}" value
    value="$(_env_get "$key" "")"
    [[ -n "$value" ]] || value="${!key-}"
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    value="$(_phase06_generate_hex_secret "$bytes")" || return 1
    printf '%s%s' "$prefix" "$value"
}

# Optional paths are only passed by the isolated WSL mount contract test.
# shellcheck disable=SC2120
_phase06_pixel_runtime_layout() {
    PIXEL_INGRESS_RUNTIME_DIR_VALUE=/run/ods-pixel
    PIXEL_PREVIEW_RUNTIME_DIR_VALUE=/run/ods-pixel-preview
    PIXEL_RUNTIME_BIND_PROPAGATION_VALUE=rprivate
    local kernel_release="${1:-/proc/sys/kernel/osrelease}" wsl_mount="${2:-/mnt/wsl}"
    [[ -r "$kernel_release" ]] || return 0
    grep -qi microsoft "$kernel_release" || return 0
    # Docker Desktop's daemon runs in a different WSL distro. /run in this
    # distro is therefore not its /run; /mnt/wsl is the shared tmpfs bridge.
    # Phase 05 may select sudo docker before a new docker group membership
    # takes effect. Probe with that same command, not an unprivileged client.
    local -a docker_command=(docker)
    case "${DOCKER_CMD:-docker}" in
        docker) ;;
        'sudo docker') docker_command=(sudo docker) ;;
        *) return 1 ;;
    esac
    # A remote daemon cannot bind this distro's /run or /mnt/wsl. Check both
    # the explicit override and the selected context before trusting its OS.
    [[ -z "${DOCKER_HOST:-}" || "${DOCKER_HOST}" == unix:///* ]] || return 1
    local docker_endpoint docker_os
    docker_endpoint="$(timeout 10s "${docker_command[@]}" context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" || return 1
    [[ "$docker_endpoint" == unix:///* ]] || return 1
    docker_os="$(timeout 10s "${docker_command[@]}" info --format '{{.OperatingSystem}}' 2>/dev/null)" || return 1
    case "$docker_os" in
        'Docker Desktop'|'Docker Desktop (containerized)') ;;
        *) return 0 ;;
    esac
    [[ -d "$wsl_mount" && "$(findmnt -n -o PROPAGATION -T "$wsl_mount")" == shared ]] || return 1
    # Use this distro's path. Docker Desktop's WSL proxy translates bind
    # sources from the calling distro; the daemon's own name for this tmpfs
    # is resolved inside this distro instead and fails as "not a shared mount".
    PIXEL_INGRESS_RUNTIME_DIR_VALUE=/mnt/wsl/ods-portal-runtime/ingress
    PIXEL_PREVIEW_RUNTIME_DIR_VALUE=/mnt/wsl/ods-portal-runtime/preview
    PIXEL_RUNTIME_BIND_PROPAGATION_VALUE=rshared
}

if $DRY_RUN; then
    log "[DRY RUN] Would create: $INSTALL_DIR/{config,data,models}"
    log "[DRY RUN] Would copy compose files ($COMPOSE_FLAGS) and source tree"
    log "[DRY RUN] Would generate .env with secrets (WEBUI_SECRET, N8N_PASS, LITELLM_KEY, etc.)"
    log "[DRY RUN] Would generate SearXNG config with randomized secret key"
    [[ "$ENABLE_HERMES" == "true" ]] && log "[DRY RUN] Would configure Hermes Agent (LLM endpoint: http://llama-server:8080/v1; data dir: $INSTALL_DIR/data/hermes)"
    log "[DRY RUN] Would validate .env against schema"
else
    # install-core.sh normally imports these helpers before the phase runs.
    # Source them defensively so isolated phase reuse has the same contract.
    if ! declare -F ods_pixel_reconcile_installed_compose >/dev/null 2>&1; then
        # shellcheck source=../lib/pixel-integration.sh
        _phase06_source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        source "$_phase06_source_dir/../lib/pixel-integration.sh"
        unset _phase06_source_dir
    fi

    # shellcheck source=../lib/llama-memory-budget.sh
    source "$SCRIPT_DIR/installers/lib/llama-memory-budget.sh"
    # shellcheck source=../lib/searxng-locale.sh
    source "$SCRIPT_DIR/installers/lib/searxng-locale.sh"

    # shellcheck source=../../lib/dotenv-quote.sh
    source "$SCRIPT_DIR/lib/dotenv-quote.sh"
    # shellcheck source=../../lib/safe-env.sh
    source "$SCRIPT_DIR/lib/safe-env.sh"

    # Older Pixel source updates (bin/pixel_source_upgrade.py, run as root) set
    # only the owner of the files and folders they wrote, so those kept root's
    # group. The host agent keeps a file's group when it rewrites one, which
    # the owner cannot do for root's group, so adding Hermes back from the
    # Extensions Library failed. Return them to the owner's group, as a fresh
    # install leaves them: the owner's own files and folders in the six source
    # trees those updates write, as the owner, never through a link. A pending
    # Pixel source plan records file contents and modes, not groups, so this
    # cannot disturb one.
    _phase06_repair_root_group() {
        local uid gid name count
        local -a roots=()
        uid="$(id -u)"
        gid="$(id -g)"
        # Files that root owns belong in group root.
        [[ "$uid" != 0 && "$gid" != 0 ]] || return 0
        for name in bin lib scripts installers extensions vendor; do
            [[ -d "$INSTALL_DIR/$name" && ! -L "$INSTALL_DIR/$name" ]] || continue
            roots+=("$INSTALL_DIR/$name")
        done
        (( ${#roots[@]} > 0 )) || return 0
        count="$(find -P "${roots[@]}" \( -type f -o -type d \) -user "$uid" -group 0 -print0 \
            | tr -cd '\0' | wc -c)" \
            || error "Could not check the installed source trees for files in group root."
        (( count > 0 )) || return 0
        find -P "${roots[@]}" \( -type f -o -type d \) -user "$uid" -group 0 \
            -exec chgrp -h "$gid" {} + \
            || error "Could not return installed source files from group root to group $gid."
        log "Returned $((count)) installed source files and folders from group root to group $gid"
    }
    _phase06_repair_root_group
    unset -f _phase06_repair_root_group

    _env_existing=""
    [[ -f "$INSTALL_DIR/.env" ]] && _env_existing="$INSTALL_DIR/.env"

    # Resolve the requested source before replacing installed code or retiring
    # a managed Pixel. Never source the owner's .env as shell code. Decode with
    # the same grammar used when its values were written so quoted values stay
    # literal across an upgrade.
    _env_get() {
        local key="$1" default="${2:-}"
        if [[ -n "$_env_existing" ]]; then
            local val
            val=$(grep -m1 "^${key}=" "$_env_existing" 2>/dev/null | cut -d= -f2- || true)
            val="$(safe_env_decode_value "$val")"
            if [[ -n "$val" ]]; then
                printf '%s\n' "$val"
                return
            fi
        fi
        printf '%s\n' "$default"
    }

    _env_get_explicit_first() {
        local key="$1" default="${2:-}" val
        val="${!key-}"
        if [[ -n "$val" ]]; then
            printf '%s\n' "$val"
            return
        fi
        _env_get "$key" "$default"
    }

    _phase06_requested_pixel_url=""
    _phase06_requested_pixel_ref=""
    _phase06_requested_pixel_dir=""
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == true ]]; then
        _phase06_requested_pixel_url="$(_env_get_explicit_first PIXEL_SOURCE_URL bundled)"
        _phase06_requested_pixel_ref="$(_env_get_explicit_first PIXEL_SOURCE_REF "$ODS_PIXEL_BUNDLED_REF")"
        _phase06_requested_pixel_dir="$(_env_get_explicit_first PIXEL_SOURCE_DIR "")"
        # A persisted bundled ref identifies the release that was installed,
        # not a pin against future ODS updates. Use this release's verified
        # bundle unless the caller explicitly requested an exact source ref.
        # The source-transition checks below still validate/retire the prior
        # managed checkout before installed code is replaced.
        if [[ "$_phase06_requested_pixel_url" == bundled && -z "${PIXEL_SOURCE_REF:-}" ]]; then
            _phase06_requested_pixel_ref="$ODS_PIXEL_BUNDLED_REF"
        fi
        # This is an upgrade sentinel only; no private repository is fetched.
        if [[ "$_phase06_requested_pixel_url" == 'https://github.com/Osmantic/Pixel.git' \
            && "$_phase06_requested_pixel_ref" == 'b33730436baf5d98bf58f7d57c090318fe19f433' ]]; then
            _phase06_requested_pixel_url=bundled
            _phase06_requested_pixel_ref="$ODS_PIXEL_BUNDLED_REF"
            ai "Migrating the former Pixel source setting to the bundled ODS release."
        fi
        [[ "$_phase06_requested_pixel_ref" =~ ^[0-9a-f]{40}$ ]] || {
            error "Pixel requires an exact source commit before an upgrade."
            return 1
        }
        if [[ "$_phase06_requested_pixel_url" == bundled ]]; then
            _phase06_source_bundle="$SCRIPT_DIR/vendor/pixel.bundle"
            [[ "$_phase06_requested_pixel_ref" == "$ODS_PIXEL_BUNDLED_REF" \
                && -f "$_phase06_source_bundle" && ! -L "$_phase06_source_bundle" \
                && "$(sha256sum -- "$_phase06_source_bundle" | cut -d ' ' -f 1)" == "$ODS_PIXEL_BUNDLED_SHA256" ]] || {
                error "The requested bundled Pixel source is absent or changed; the installed release was left intact."
                return 1
            }
            unset _phase06_source_bundle
        elif [[ "$_phase06_requested_pixel_url" == /* ]]; then
            PIXEL_SOURCE_URL="$_phase06_requested_pixel_url" \
                PIXEL_SOURCE_REF="$_phase06_requested_pixel_ref" \
                PIXEL_SOURCE_DIR="$_phase06_requested_pixel_dir" \
                ods_pixel_validate_source || {
                    error "The requested local Pixel source is invalid; the installed release was left intact."
                    return 1
                }
            env -i PATH="$PATH" HOME="$HOME" GIT_CONFIG_NOSYSTEM=1 \
                GIT_CONFIG_GLOBAL=/dev/null GIT_ALLOW_PROTOCOL=file GIT_NO_REPLACE_OBJECTS=1 \
                git -C "$_phase06_requested_pixel_url" cat-file -e \
                "${_phase06_requested_pixel_ref}^{commit}" || {
                    error "The requested local Pixel commit is unavailable; the installed release was left intact."
                    return 1
                }
        else
            error "Pixel source must be bundled or an absolute local checkout; the installed release was left intact."
            return 1
        fi
    fi

    # Retired bundled services. The source copy below never deletes files a
    # release removed, so an upgraded install still carries their extension
    # directories, and a stale compose.yaml there would keep the old container
    # in the stack. Delete only the service code and untouched shipped
    # templates; each service's data stays for the owner to archive or delete.
    #
    # The Pixel source transaction below records the installed extensions tree
    # as its baseline and checks it again before release. The prune therefore
    # runs before a new plan is staged, but never while an unfinished plan is
    # pending: that plan is bound to the tree it recorded, and only the release
    # that staged it can finish, resume or roll it back.
    _phase06_retire_openclaw_config() {
        # Every release copied the OpenClaw templates into config/openclaw,
        # whether the extension was used or not. A template that is still
        # byte-identical to a shipped version is not owner data.
        local config="$INSTALL_DIR/config/openclaw" data="$INSTALL_DIR/data/openclaw"
        local manifest="$SCRIPT_DIR/installers/lib/retired-openclaw-config.sha256"
        local digest relative path folders=""
        if [[ -d "$config" && ! -L "$config" && -f "$manifest" ]]; then
            while read -r digest relative; do
                # A source tree copied from a Windows checkout has CRLF lines.
                relative="${relative%$'\r'}"
                [[ -n "$digest" && "$digest" != \#* && "$relative" != *..* ]] || continue
                path="$config/$relative"
                # Never reach a template through a linked folder.
                [[ "$relative" != */* || ! -L "$config/${relative%/*}" ]] || continue
                [[ -f "$path" && ! -L "$path" ]] || continue
                # An unreadable file has no digest, so it is kept.
                [[ "$(sha256sum -- "$path" 2>/dev/null | cut -d ' ' -f 1)" == "$digest" ]] || continue
                rm -f -- "$path" || log "Could not remove the unchanged OpenClaw template $path (non-fatal)"
            done < "$manifest"
            for path in "$config/workspace" "$config"; do
                if [[ -d "$path" && ! -L "$path" && -r "$path" && -x "$path" && -z "$(ls -A -- "$path")" ]]; then
                    rmdir -- "$path" || log "Could not remove the empty folder $path (non-fatal)"
                fi
            done
        fi
        if [[ -e "$config" || -L "$config" ]]; then
            folders="config/openclaw"
        fi
        # An unreadable data folder may still hold the agent's state.
        if [[ -d "$data" ]]; then
            if [[ ! -r "$data" || ! -x "$data" ]] || [[ -n "$(ls -A -- "$data")" ]]; then
                folders="${folders:+$folders and }data/openclaw"
            fi
        fi
        if [[ -n "$folders" ]]; then
            ai "The legacy OpenClaw extension was removed. Its remaining files in $folders were kept; delete them by hand when you no longer need them (docs/MIGRATION-OPENCLAW-TO-HERMES.md explains how)."
            if [[ -f "$HOME/.config/systemd/user/memory-shepherd-memory.timer" \
                || -f "$HOME/.config/systemd/user/memory-shepherd-workspace.timer" ]]; then
                ai "Disable the memory-shepherd-memory and memory-shepherd-workspace user timers before deleting config/openclaw; they still maintain its workspace."
            fi
        fi
    }

    _phase06_retire_lemonade_files() {
        # ODS stopped shipping these with the Lemonade runtime. The source copy
        # never deletes a file, so an upgraded install still has them. A file
        # that is byte-identical to a shipped version is not owner data.
        local manifest="$SCRIPT_DIR/installers/lib/retired-lemonade-files.sha256"
        local digest relative path kept=""
        [[ -f "$manifest" ]] || return 0
        while read -r digest relative; do
            relative="${relative%$'\r'}"
            [[ -n "$digest" && "$digest" != \#* && "$relative" != *..* ]] || continue
            # The rendered LiteLLM map is checked by the helper below.
            [[ "$relative" != config/litellm/lemonade.yaml ]] || continue
            path="$INSTALL_DIR/$relative"
            [[ -f "$path" && ! -L "$path" ]] || continue
            # An unreadable file has no digest, so it is kept.
            [[ "$(sha256sum -- "$path" 2>/dev/null | cut -d ' ' -f 1)" == "$digest" ]] || continue
            rm -f -- "$path" || log "Could not remove the retired Lemonade file $path (non-fatal)"
        done < "$manifest"
        for relative in docker-compose.lemonade-external.yml \
            extensions/services/llama-server/Dockerfile.amd \
            extensions/services/llama-server/lemonade-entrypoint.sh \
            scripts/select-external-lemonade-model.py \
            config/litellm/strix-halo-config.yaml; do
            [[ ! -e "$INSTALL_DIR/$relative" ]] || kept="${kept:+$kept, }$relative"
        done
        [[ -z "$kept" ]] || ai "Kept edited Lemonade-era files: $kept. Nothing uses them now; delete them when you no longer need them."
        if [[ -f "$INSTALL_DIR/config/litellm/lemonade.yaml" ]]; then
            "${ODS_PYTHON_CMD:-python3}" "$SCRIPT_DIR/scripts/migrate-lemonade-install.py" retire-render \
                --install-dir "$INSTALL_DIR" >> "$LOG_FILE" 2>&1 \
                || log "Could not check config/litellm/lemonade.yaml for retirement (non-fatal; nothing reads it)"
        fi
    }

    _phase06_prune_retired_services() {
        _phase06_step "prune-retired-services"
        # ODSForge was retired from the shipped stack after Hermes became the
        # default agent surface; data/odsforge is preserved.
        if [[ -d "$INSTALL_DIR/extensions/services/odsforge" ]]; then
            rm -rf "$INSTALL_DIR/extensions/services/odsforge"
            log "Removed retired ODSForge service files from extensions/services"
        fi
        # The legacy OpenClaw extension (the ods-openclaw container) was
        # removed; Portal (Pixel) and Hermes are the supported agents. Once it
        # is out of the stack, phase 11's `up --remove-orphans` removes the old
        # container.
        if [[ -d "$INSTALL_DIR/extensions/services/openclaw" ]]; then
            rm -rf "$INSTALL_DIR/extensions/services/openclaw"
            log "Removed retired OpenClaw service files from extensions/services"
        fi
        _phase06_retire_openclaw_config
        _phase06_retire_lemonade_files
    }

    # A Pixel-to-Hermes rerun must retire the exact ODS-managed host runtime,
    # not merely remove the Compose edge from the next launch. Do this before
    # copying new source over an existing install so the fail-closed cleanup can
    # still compare root-owned artifacts with the source that installed them.
    _phase06_pixel_marker="$HOME/.config/ods/pixel-managed.json"
    _phase06_pixel_source_transition=0
    # Cleared below while an unfinished Pixel source plan is pending.
    _phase06_prune_ready=true
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" \
        && ( -e "$_phase06_pixel_marker" || -L "$_phase06_pixel_marker" ) ]]; then
        # Fail before source restoration, pruning or staging. Rootless fallback
        # is supported for optional extras, not an existing Pixel deployment.
        if [[ ${EUID:-$(id -u)} -ne 0 && "${ODS_SUDO_AVAILABLE:-true}" == false ]]; then
            error "source-upgrade-sudo-required: Updating the existing Pixel installation requires sudo. Preserve the installation and any pending upgrade state."
            return 1
        fi
        _phase06_pixel_owner="$(ods_pixel_install_owner)" || {
            error "Could not identify the ODS owner for a Pixel source transition."
            return 1
        }
        _phase06_pixel_home="$(ods_pixel_owner_home "$_phase06_pixel_owner")" || {
            error "Could not resolve the ODS owner home for a Pixel source transition."
            return 1
        }
        # Name the failed step even if the child was interrupted before it
        # could emit a diagnostic. Do not promise a reason was printed.
        _phase06_source_failed() {
            error "The Pixel source update stopped at its '$1' step. Preserve the installation and any pending upgrade state."
            return 1
        }
        _ods_pixel_source_transition_required \
            "$_phase06_pixel_owner" "$_phase06_pixel_home" "$_phase06_requested_pixel_ref" "$SCRIPT_DIR" \
            || _phase06_pixel_source_transition=$?
        if [[ "$_phase06_pixel_source_transition" == 0 || "$_phase06_pixel_source_transition" == 1 ]] \
            && ods_sudo test -d /var/lib/ods-pixel-access/source-upgrade; then
            _phase06_source_status="$(_ods_pixel_source_upgrade status "$_phase06_pixel_owner")" \
                || _phase06_source_failed status || return 1
            if jq -e '.pending == true and .phase == "complete"' <<<"$_phase06_source_status" >/dev/null; then
                _ods_pixel_source_upgrade finish "$_phase06_pixel_owner" || _phase06_source_failed finish || return 1
            elif jq -e '.pending == true' <<<"$_phase06_source_status" >/dev/null; then
                _phase06_pixel_source_transition=0
                # The pending plan is bound to the tree it recorded. Keep the
                # retired service files: `stage` below either resumes this
                # release's own plan, which pruned them before staging, or
                # refuses an older plan so the release that staged it can
                # still finish or roll it back.
                _phase06_prune_ready=false
            fi
            unset _phase06_source_status
        fi
        case "$_phase06_pixel_source_transition" in
            0)
                _phase06_step "rebind-pixel-source"
                ai "Preparing the verified Pixel source upgrade while preserving its access mode..."
                if ! _ods_pixel_restore_transition_source \
                    "$_phase06_pixel_owner" "$_phase06_pixel_home" "$_phase06_requested_pixel_ref" >/dev/null; then
                    error "Could not verify the prior Pixel checkout for safe upgrade. Restore its local backup before retrying; no private repository was contacted."
                    return 1
                fi
                if [[ "$_phase06_prune_ready" == true ]]; then
                    _phase06_prune_retired_services
                fi
                if ! _ods_pixel_source_upgrade stage "$_phase06_pixel_owner" \
                    "$SCRIPT_DIR" "$_phase06_requested_pixel_ref"; then
                    error "Could not stage the exact Pixel source upgrade; the active source and access state were left intact."
                    return 1
                fi
                _phase06_pixel_binary="$(_ods_pixel_openclaw_bin "$_phase06_pixel_owner" "$_phase06_pixel_home")" \
                    || _phase06_source_failed locate-pixel || return 1
                # Install the source-release guard in the protected controller
                # before taking its hold. The helper journals every mirror
                # replacement first and keeps the actual installation binding.
                _phase06_source_status="$(_ods_pixel_source_upgrade status "$_phase06_pixel_owner")" \
                    || _phase06_source_failed status || return 1
                if jq -e '.transaction == null and .phase == "staged"' <<<"$_phase06_source_status" >/dev/null; then
                    _ods_pixel_install_access_service "$_phase06_pixel_owner" \
                        "$_phase06_pixel_binary" false true "$SCRIPT_DIR" || _phase06_source_failed access-service || return 1
                fi
                # Past the downstream boundary the update can only go forward,
                # possibly under this corrected installer, which took over a
                # failed one at stage. Its coordinator replaces the previous
                # one once this tree is applied, before anything else runs.
                _phase06_source_downstream=false
                if jq -e '.downstream == true' <<<"$_phase06_source_status" >/dev/null; then
                    _phase06_source_downstream=true
                    ai "Resuming the interrupted Pixel source upgrade under its existing admission hold..."
                fi
                unset _phase06_source_status
                ODS_PIXEL_SOURCE_TRANSACTION="$(_ods_pixel_source_upgrade hold "$_phase06_pixel_owner")" \
                    || _phase06_source_failed hold || return 1
                if [[ ! "$ODS_PIXEL_SOURCE_TRANSACTION" =~ ^[a-f0-9]{64}$ ]]; then
                    error "source-hold-response-invalid: The Pixel source hold did not return a valid transaction identifier. Preserve any existing admission hold and upgrade state; no source copy was started."
                    return 1
                fi
                export ODS_PIXEL_SOURCE_TRANSACTION
                _ods_pixel_source_upgrade copy "$_phase06_pixel_owner" || _phase06_source_failed copy || return 1
                # Everything after this boundary can update Compose/env/data
                # and native services. Recovery must resume this same candidate;
                # a source-only rollback would no longer restore the installer.
                _ods_pixel_source_upgrade downstream "$_phase06_pixel_owner" || _phase06_source_failed downstream || return 1
                if [[ "$_phase06_source_downstream" == true ]]; then
                    _ods_pixel_install_access_service "$_phase06_pixel_owner" \
                        "$_phase06_pixel_binary" false true || _phase06_source_failed access-service || return 1
                fi
                unset _phase06_pixel_binary _phase06_source_downstream
                ;;
            1)
                _phase06_prune_retired_services
                ;;
            *)
                error "The existing ODS-managed Pixel state is unsafe for a source transition."
                return 1
                ;;
        esac
        unset -f _phase06_source_failed
        unset _phase06_pixel_owner _phase06_pixel_home
    elif [[ "${ENABLE_PIXEL_RUNTIME:-false}" != "true" \
        && ( -e "$_phase06_pixel_marker" || -L "$_phase06_pixel_marker" ) ]]; then
        if ! declare -F ods_pixel_uninstall_managed >/dev/null 2>&1; then
            # shellcheck source=../../lib/pixel-uninstall.sh
            source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
        fi
        _phase06_step "deactivate-pixel"
        if ! ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME"; then
            error "Could not safely deactivate the ODS-managed Pixel host runtime."
            return 1
        fi
        _phase06_prune_retired_services
    else
        _phase06_prune_retired_services
    fi
    unset _phase06_pixel_marker _phase06_pixel_source_transition _phase06_prune_ready
    unset -f _phase06_prune_retired_services _phase06_retire_openclaw_config _phase06_retire_lemonade_files

    _phase06_rootless=false
    if [[ -f "$SCRIPT_DIR/lib/rootless-ownership.sh" ]]; then
        # shellcheck source=../../lib/rootless-ownership.sh
        source "$SCRIPT_DIR/lib/rootless-ownership.sh"
        _phase06_rootless_state=0
        ods_docker_rootless_state || _phase06_rootless_state=$?
        case "$_phase06_rootless_state" in
            0) _phase06_rootless=true ;;
            1) ;;
            *)
                error "Could not determine Docker rootless mode. Verify Docker access, then re-run the installer."
                return 1
                ;;
        esac
    fi

    _phase06_compose_uid=$(_env_get ODS_UID "")
    _phase06_compose_gid=$(_env_get ODS_GID "")
    # Migrate the old Compose-only UID/GID keys without writing Bash's
    # readonly UID variable back into the generated dotenv file.
    [[ -n "$_phase06_compose_uid" ]] \
        || _phase06_compose_uid=$(_env_get UID "${SUDO_UID:-$(id -u)}")
    [[ -n "$_phase06_compose_gid" ]] \
        || _phase06_compose_gid=$(_env_get GID "${SUDO_GID:-$(id -g)}")
    [[ "$_phase06_compose_uid" =~ ^[0-9]+$ ]] \
        || error "ODS_UID must be a non-negative integer, got: $_phase06_compose_uid"
    [[ "$_phase06_compose_gid" =~ ^[0-9]+$ ]] \
        || error "ODS_GID must be a non-negative integer, got: $_phase06_compose_gid"

    # Create directories
    _phase06_step "create-directories"
    ods_progress 38 "directories" "Creating directory structure"
    mkdir -p "$INSTALL_DIR"/{config,data,models}
    mkdir -p "$INSTALL_DIR"/data/{open-webui,whisper,tts,n8n,qdrant,models,privacy-shield,ape,token-spy,hermes,persona}
    mkdir -p "$INSTALL_DIR"/data/hermes-proxy/{caddy-data,caddy-config}
    mkdir -p "$INSTALL_DIR"/data/langfuse/{postgres,clickhouse,redis,minio}
    mkdir -p "$INSTALL_DIR"/data/remote-provider/secrets
    mkdir -p "$INSTALL_DIR"/config/{n8n,litellm,searxng}

    _phase06_repair_host_path() {
        local target="$1" description="$2" target_parent

        if $_phase06_rootless; then
            local relative="${target#"$INSTALL_DIR"/}"
            if [[ "$relative" == "$target" ]] || \
               ! ods_rootless_make_host_writable "$INSTALL_DIR" "$relative"; then
                error "Failed to repair $description in the rootless namespace: $target"
                return 1
            fi
            return 0
        fi
        if ! ods_sudo_available; then
            # A rootful Docker daemon can repair a container-owned ODS path
            # through an exact bind mount without granting host sudo. Never
            # follow a replaced top-level directory or an arbitrary path.
            target_parent="${target%/}"
            target_parent="${target_parent%/*}"
            if [[ "$target_parent" != "$INSTALL_DIR/data" \
               && "$target_parent" != "$INSTALL_DIR/config" ]] \
               || [[ ! -d "$target" || -L "${target%/}" \
                   || -L "$target_parent" || -L "$INSTALL_DIR" ]]; then
                error "Refusing unsafe $description repair: $target"
                return 1
            fi
            _ods_rootless_ensure_helper_image || return 1
            if ! docker_run run --rm --network none --user 0:0 \
                --mount "type=bind,src=${target%/},dst=/data" \
                "$ODS_ROOTLESS_HELPER_IMAGE" chown -h -R "$(id -u):$(id -g)" /data; then
                error "Could not repair $description with scoped Docker access: $target"
                return 1
            fi
            [[ -w "$target" ]] || {
                error "Repaired $description is still not writable: $target"
                return 1
            }
            return 0
        fi
        if ! ods_sudo chown -R "$(id -u):$(id -g)" "$target" 2>/dev/null; then
            error "Failed to repair $description: $target"
            return 1
        fi
    }

    # Hermes remaps its in-container user to the persisted host UID/GID and
    # keeps HERMES_HOME at data/hermes mounted as /opt/data.
    # Upstream intentionally makes that directory 0700. A reinstall running
    # as the host user must not "repair" it back to uid 1000, or Hermes's web
    # status and ODS Talk JSON-RPC paths fail with PermissionError.
    if ! $_phase06_rootless \
        && [[ "${ENABLE_HERMES:-false}" == "true" && -d "$INSTALL_DIR/data/hermes" ]]; then
        _hermes_metadata=$(stat -c '%u:%g:%a' "$INSTALL_DIR/data/hermes" 2>/dev/null || true)
        if [[ "$_hermes_metadata" != "$_phase06_compose_uid:$_phase06_compose_gid:700" ]]; then
            # A fresh no-sudo install creates this directory as the invoking
            # user, often with mode 755/775. That user can make it private
            # directly; privileged repair is only needed for foreign owners.
            if [[ "${_hermes_metadata%:*}" == "$_phase06_compose_uid:$_phase06_compose_gid" ]] \
                && chmod 700 "$INSTALL_DIR/data/hermes" 2>/dev/null; then
                :
            else
                if ! ods_sudo_available; then
                    error "Hermes requires data/hermes ownership $_phase06_compose_uid:$_phase06_compose_gid and mode 700 with a rootful runtime. Grant privileged access or disable Hermes, then re-run ODS."
                    return 1
                fi
                ods_sudo chown -R "$_phase06_compose_uid:$_phase06_compose_gid" "$INSTALL_DIR/data/hermes" 2>/dev/null || {
                    error "Failed to restore data/hermes ownership to $_phase06_compose_uid:$_phase06_compose_gid"
                    return 1
                }
                ods_sudo chmod 700 "$INSTALL_DIR/data/hermes" 2>/dev/null || {
                    error "Failed to preserve private mode 700 on data/hermes"
                    return 1
                }
            fi
        fi
        unset _hermes_metadata
    fi

    # Fix ownership of data/config dirs that may have been created by containers
    # (e.g. SearXNG runs as uid 977, ComfyUI data owned by root)
    if ! $_phase06_rootless; then
        for _data_dir in "$INSTALL_DIR"/data/*/; do
            [[ "${ENABLE_HERMES:-false}" == "true" && "$_data_dir" == "$INSTALL_DIR/data/hermes/" ]] && continue
            # Private chat results and image history belong to Dashboard UID 1000.
            [[ "$_data_dir" == "$INSTALL_DIR/data/pixel-chat-results/" ]] && continue
            [[ "$_data_dir" == "$INSTALL_DIR/data/pixel-images/" ]] && continue
            # Token Spy's persistent directory intentionally belongs to its
            # container UID 1000; phase 06 verifies that identity below.
            [[ "$_data_dir" == "$INSTALL_DIR/data/token-spy/" ]] && continue
            # APE's private governance state/audit directory intentionally
            # belongs to the APE container UID; phase 06 prepares it below.
            [[ "$_data_dir" == "$INSTALL_DIR/data/ape/" ]] && continue
            if [[ -d "$_data_dir" ]] && ! [[ -w "$_data_dir" ]]; then
                _phase06_repair_host_path "$_data_dir" "container-owned data directory" || return 1
            fi
        done
    fi
    for _cfg_dir in "$INSTALL_DIR"/config/*/; do
        if [[ -d "$_cfg_dir" ]] && ! [[ -w "$_cfg_dir" ]]; then
            _phase06_repair_host_path "$_cfg_dir" "container-owned config directory" || return 1
        fi
    done

    # Ensure we can write to config/data subtrees (rsync will fail otherwise)
    if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
        _cant_write=""
        _phase06_write_roots="config data"
        $_phase06_rootless && _phase06_write_roots="config"
        for _root in $_phase06_write_roots; do
            [[ -d "$INSTALL_DIR/$_root" ]] || continue
            for _d in "$INSTALL_DIR/$_root"/*/; do
                [[ "${ENABLE_HERMES:-false}" == "true" && "$_d" == "$INSTALL_DIR/data/hermes/" ]] && continue
                [[ "$_d" == "$INSTALL_DIR/data/pixel-chat-results/" ]] && continue
                [[ "$_d" == "$INSTALL_DIR/data/pixel-images/" ]] && continue
                [[ "$_d" == "$INSTALL_DIR/data/token-spy/" ]] && continue
                [[ "$_d" == "$INSTALL_DIR/data/ape/" ]] && continue
                [[ -d "$_d" ]] && ! [[ -w "$_d" ]] && _cant_write="$_cant_write ${_d#"$INSTALL_DIR"/}"
            done
        done
        if [[ -n "$_cant_write" ]]; then
            error "Cannot write to directories (likely container-owned):$_cant_write

Fix with: sudo chown -R \$(id -u):\$(id -g) $INSTALL_DIR/config $INSTALL_DIR/data — then re-run the installer."
        fi
    fi

    # Copy entire source tree to install dir (skip if same directory)
    _phase06_step "copy-source"
    ods_progress 39 "directories" "Copying source files"
    if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
        ai "Copying source files to $INSTALL_DIR..."
        # shellcheck source=../lib/source-copy.sh
        source "$SCRIPT_DIR/installers/lib/source-copy.sh"
        ods_copy_install_source "$SCRIPT_DIR" "$INSTALL_DIR" "$LOG_FILE" || {
            error "Source upgrade failed; existing cloud provider configuration was preserved."
            return 1
        }
        # Ensure scripts are executable
        if [[ -n "${ODS_PIXEL_SOURCE_TRANSACTION:-}" ]]; then
            # Protected source modes were installed from the exact staged
            # inventory; do not mutate them after hashing, including custom
            # scripts retained from the previous installation.
            chmod +x "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/ods-cli 2>>"$LOG_FILE" || warn "Some scripts may not be executable — verify after install"
        else
            chmod +x "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/ods-cli 2>>"$LOG_FILE" || warn "Some scripts may not be executable — verify after install"
        fi
        ai_ok "Source files installed"
    else
        log "Running in-place (source == install dir), skipping file copy"
    fi

    if declare -F _ods_apply_deferred_feature_state >/dev/null; then
        _ods_apply_deferred_feature_state "$_phase06_requested_pixel_ref" || {
            error "Deferred feature reconciliation failed; resume the same installer candidate."
            return 1
        }
    fi

    # A Windows-mounted WSL checkout can surface every source entry as 0777.
    # Product config and extension code must never remain ambiently writable
    # after installation. Do not follow links; downstream trust checks reject
    # any link where a regular file or directory is required.
    for _installed_code_root in \
        "$INSTALL_DIR/bin" \
        "$INSTALL_DIR/lib" \
        "$INSTALL_DIR/scripts" \
        "$INSTALL_DIR/installers" \
        "$INSTALL_DIR/config" \
        "$INSTALL_DIR/extensions" \
        "$INSTALL_DIR/vendor"
    do
        [[ -d "$_installed_code_root" && ! -L "$_installed_code_root" ]] \
            || error "Missing or unsafe installed code tree: $_installed_code_root"
        find -P "$_installed_code_root" \( -type d -o -type f \) \
            \( -perm -020 -o -perm -002 \) -exec chmod go-w {} + \
            || error "Could not secure installed code tree: $_installed_code_root"
    done
    find -P "$INSTALL_DIR" -maxdepth 1 -type f \
        \( -name '*.sh' -o -name 'ods-cli' \) \
        \( -perm -020 -o -perm -002 \) -exec chmod go-w {} + \
        || error "Could not secure installed root executables"
    [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] || error "Unsafe installed root"
    chmod go-w "$INSTALL_DIR" || error "Could not secure installed root"
    unset _installed_code_root

    # Source staging under umask 077 makes the two public policy bind mounts
    # unreadable to APE and remote-provider-egress, which run as non-root.
    # Normalize them on fresh and retained installs before Compose starts.
    _phase06_step "prepare-public-policy-mounts"
    if ! bash "$INSTALL_DIR/scripts/prepare-public-policy-mounts.sh" "$INSTALL_DIR" \
        >> "$LOG_FILE" 2>&1; then
        error "Could not prepare public policy mounts for non-root services. See $LOG_FILE for details."
        return 1
    fi

    # Windows-mounted WSL checkouts commonly present every copied file as
    # mode 0777 even when Git records a narrower executable bit. Pixel refuses
    # group/other-writable execution controls by design, so normalize only the
    # two reviewed helpers that are copied into its owner-private runtime.
    _pixel_exec_control_dir="$INSTALL_DIR/extensions/services/pixel-agent/host"
    for _pixel_exec_control in cancellable-exec.sh noninteractive-sudo.sh; do
        _pixel_exec_control_path="$_pixel_exec_control_dir/$_pixel_exec_control"
        [[ -f "$_pixel_exec_control_path" && ! -L "$_pixel_exec_control_path" ]] \
            || error "Missing or unsafe Pixel execution-control helper: $_pixel_exec_control_path"
        chmod 0755 "$_pixel_exec_control_path" \
            || error "Could not secure Pixel execution-control helper: $_pixel_exec_control_path"
    done
    unset _pixel_exec_control_dir _pixel_exec_control _pixel_exec_control_path

    _phase06_step "reconcile-pixel-compose"
    if ! ods_pixel_reconcile_installed_compose "$SCRIPT_DIR" "$INSTALL_DIR" "${ENABLE_PIXEL_RUNTIME:-false}"; then
        error "Could not reconcile the installed Pixel Compose fragment with the selected Pixel enablement"
    fi

    # Copy extensions library to data dir for dashboard portal.
    # Source resolution: dev installs and full checkouts read the product-owned
    # library under extensions/library/. Bootstrap installs also get the same
    # templates bundled by get-ods.sh under extensions-library-bundle/.
    # Without one of these paths, dashboard-api's /api/extensions/{id}/install
    # endpoint returns 503 "Extensions library is unavailable" and the
    # dashboard's Extensions page is non-functional.
    _phase06_step "copy-extensions-library"
    _ext_lib_src=""
    for _candidate in \
        "$SCRIPT_DIR/extensions/library/services" \
        "$INSTALL_DIR/extensions/library/services" \
        "$INSTALL_DIR/extensions-library-bundle/services"
    do
        if [[ -d "$_candidate" ]]; then _ext_lib_src="$_candidate"; break; fi
    done
    if [[ -n "$_ext_lib_src" ]]; then
        ods_copy_extensions_library "$_ext_lib_src" "$INSTALL_DIR/data" \
            || error "Could not secure the installed extension library"
        ai_ok "Extensions library copied to data/extensions-library/ (from $_ext_lib_src)"
    else
        ai_warn "Extensions library not found; dashboard Extensions page will return 503 until populated"
    fi

    _phase06_step "prepare-dashboard-permissions"
    # shellcheck source=../lib/dashboard-data.sh
    source "$SCRIPT_DIR/installers/lib/dashboard-data.sh"
    if ! ods_prepare_dashboard_data "$INSTALL_DIR" "$_phase06_rootless"; then
        error "Could not prepare Dashboard data for passwords and Portal chat results. Verify privileged Docker/host access, then re-run the installer."
        return 1
    fi

    # Prepare service-specific ownership after compose selection is final.
    _phase06_step "prepare-service-permissions"
    if ! $_phase06_rootless && [[ -f "$INSTALL_DIR/extensions/services/token-spy/compose.yaml" ]]; then
        # The image runs as UID 1000, independently of the installer owner.
        # A warning here leaves a healthy-looking service unable to store usage.
        # Do not use ods_sudo when unavailable: it deliberately returns success
        # for skipped optional commands. A matching owner can still chown directly.
        _token_spy_chown=(chown -R 1000:1000 "$INSTALL_DIR/data/token-spy")
        if ods_sudo_available; then
            _token_spy_chown=(ods_sudo "${_token_spy_chown[@]}")
        elif [[ "$(id -u)" != 1000 ]]; then
            # Docker access can perform this scoped repair without host sudo.
            [[ -d "$INSTALL_DIR/data/token-spy" && ! -L "$INSTALL_DIR/data/token-spy" ]] || {
                error "Cannot safely prepare data/token-spy: expected a real directory."
                return 1
            }
            _ods_rootless_ensure_helper_image || return 1
            _token_spy_chown=(docker_run run --rm --network none --user 0:0
                --mount "type=bind,src=$INSTALL_DIR/data/token-spy,dst=/data"
                "$ODS_ROOTLESS_HELPER_IMAGE" chown -h -R 1000:1000 /data)
        fi
        if ! "${_token_spy_chown[@]}"; then
            error "Cannot prepare data/token-spy for container UID 1000. Grant privileged access or repair its ownership, then re-run the installer."
            return 1
        fi
        unset _token_spy_chown
    fi

    # APE (Agent Policy Engine) persists private governance state and the
    # audit log to the data/ape bind mount. Its image runs as the system user
    # created by `adduser --system --no-create-home ape`, which on the pinned
    # python:3.12-slim base resolves to UID 100 / GID 65534 (nogroup),
    # independently of the installer owner. A rootful install would otherwise
    # leave data/ape owned by the invoking account under the invoking umask, so
    # the container cannot create state.json/audit.jsonl and crash-loops with
    # PermissionError. Prepare the private state directory for the APE
    # container UID/GID without a broad chmod 777 and without following
    # symlinks (chown -h so a link is never dereferenced; no -R across a
    # symlinked ancestor because install, data, and ape must be physical directories).
    # Scope: only data/ape. This block is a no-op on rootless installs, which
    # ods_fix_rootless_ownership prepares separately.
    if ! $_phase06_rootless \
        && [[ -f "$INSTALL_DIR/extensions/services/ape/compose.yaml" ]] \
        && grep -Eq '^[[:space:]]*-?[[:space:]]*(\./)?data/ape:/data/ape(:[^[:space:]]*)?[[:space:]]*$' \
            "$INSTALL_DIR/extensions/services/ape/compose.yaml"; then
        _ape_uid=100
        _ape_gid=65534
        # These IDs match the pinned image and the rootless repair contract.
        # Refuse links in the bind source and its install-owned ancestry before
        # privileged recursive ownership changes.
        [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" \
            && -d "$INSTALL_DIR/data" && ! -L "$INSTALL_DIR/data" \
            && -d "$INSTALL_DIR/data/ape" && ! -L "$INSTALL_DIR/data/ape" ]] || {
            error "Cannot safely prepare data/ape: expected real install, data, and APE directories."
            return 1
        }
        if ods_sudo_available; then
            ods_sudo chown -h -R "$_ape_uid:$_ape_gid" "$INSTALL_DIR/data/ape" \
                && ods_sudo chmod 700 "$INSTALL_DIR/data/ape" || {
                error "Cannot prepare data/ape for APE container UID $_ape_uid. Grant privileged access or repair its ownership, then re-run the installer."
                return 1
            }
        else
            _ods_rootless_ensure_helper_image || return 1
            if ! docker_run run --rm --network none --user 0:0 \
                --mount "type=bind,src=$INSTALL_DIR/data/ape,dst=/data" \
                "$ODS_ROOTLESS_HELPER_IMAGE" sh -ec '
                    chown -h -R "$1:$2" /data
                    chmod 700 /data
                ' sh "$_ape_uid" "$_ape_gid"; then
                error "Cannot prepare data/ape for APE container UID $_ape_uid. Grant privileged access or repair its ownership, then re-run the installer."
                return 1
            fi
        fi
        _ape_meta=$(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape" 2>/dev/null || true)
        if [[ "$_ape_meta" != "$_ape_uid:$_ape_gid:700" ]]; then
            error "data/ape ownership/mode verification failed: got ${_ape_meta:-unreadable}, expected $_ape_uid:$_ape_gid:700."
            return 1
        fi
        unset _ape_meta _ape_uid _ape_gid
    fi

    # ── .env merge logic: preserve user-configured values on re-install ──
    _phase06_step "generate-env"
    ods_progress 40 "directories" "Generating secrets and configuration"
    # If an existing .env exists, read user-editable values so we don't
    # destroy API keys, custom ports, or manually-set secrets.
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        log "Found existing .env — preserving user-configured values"
    fi

    # Optional overrides use an empty value to mean "inherit the bundled
    # provider". Preserve that explicit state across reruns; _env_get treats
    # empty as missing for variables whose defaults must be backfilled.
    _env_get_preserve_empty() {
        local key="$1" default="${2:-}" val
        if [[ -n "$_env_existing" ]] && grep -q -m1 "^${key}=" "$_env_existing" 2>/dev/null; then
            val=$(grep -m1 "^${key}=" "$_env_existing" 2>/dev/null | cut -d= -f2- || true)
            safe_env_decode_value "$val"
            printf '\n'
            return
        fi
        printf '%s\n' "$default"
    }

    # Resolve every fixed service-port assignment before regenerating .env.
    # Export the resolved values too: later phases use the service registry,
    # which otherwise retains the defaults it loaded before this rerun.
    while read -r _port_key _port_default; do
        _port_value="$(_env_get_explicit_first "$_port_key" "$_port_default")"
        if [[ ! "$_port_value" =~ ^[1-9][0-9]{0,4}$ ]] || (( 10#$_port_value > 65535 )); then
            error "$_port_key must be a port from 1 to 65535"
            return 1
        fi
        printf -v "${_port_key}_VALUE" '%s' "$_port_value"
        printf -v "$_port_key" '%s' "$_port_value"
        export "$_port_key"
    done <<'SERVICE_PORT_DEFAULTS'
DASHBOARD_PORT 3001
DASHBOARD_REMOTE_PORT 3011
WEBUI_PORT 3000
PERPLEXICA_PORT 3004
TTS_PORT 8880
N8N_PORT 5678
QDRANT_PORT 6333
QDRANT_GRPC_PORT 6334
EMBEDDINGS_PORT 8090
LITELLM_PORT 4000
HERMES_PROXY_PORT 9120
SERVICE_PORT_DEFAULTS
    declare -F sr_resolve_ports >/dev/null 2>&1 && sr_resolve_ports
    unset _port_key _port_default _port_value
    N8N_WEBHOOK_URL_VALUE="$(_env_get_explicit_first N8N_WEBHOOK_URL "http://localhost:${N8N_PORT_VALUE}")"

    # The local llama-server port may already belong to another owner service
    # (for example a fleet worker). Honor an explicit install override before
    # preserving an older .env value, and reject malformed ports before Compose.
    OLLAMA_PORT_VALUE="$(_env_get_explicit_first OLLAMA_PORT "11434")"
    if [[ ! "$OLLAMA_PORT_VALUE" =~ ^[1-9][0-9]{0,4}$ ]] \
        || (( 10#$OLLAMA_PORT_VALUE > 65535 )); then
        error "OLLAMA_PORT must be a port from 1 to 65535"
        return 1
    fi
    # Keep the selected SearXNG origin consistent across Compose and Pixel on
    # a rerun. An explicit port override wins over the retained installed port.
    SEARXNG_PORT_VALUE="$(_env_get_explicit_first SEARXNG_PORT 8888)"
    if [[ ! "$SEARXNG_PORT_VALUE" =~ ^[1-9][0-9]{0,4}$ ]] \
        || (( 10#$SEARXNG_PORT_VALUE > 65535 )); then
        error "SEARXNG_PORT must be a port from 1 to 65535"
        return 1
    fi
    SEARXNG_PORT="$SEARXNG_PORT_VALUE"

    # Secrets: reuse existing values, generate only if missing
    WEBUI_SECRET=$(_phase06_env_hex_secret WEBUI_SECRET 32)
    N8N_PASS=$(_env_get N8N_PASS "$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64)")
    LITELLM_KEY=$(_phase06_env_hex_secret LITELLM_KEY 16 "sk-ods-")
    ODS_WINDOWS_SYSTEM_DIRECTORY="$(_env_get_explicit_first ODS_WINDOWS_SYSTEM_DIRECTORY '')"
    ODS_WSL_STATE_ROOT="$(_env_get_explicit_first ODS_WSL_STATE_ROOT '')"
    # Host-native llama-server (Windows Portal): the in-stack llama-server is
    # off, and LiteLLM and model-router reach the native server with
    # LLAMA_SERVER_API_KEY. install-core.sh validated the flags.
    NATIVE_LLM_ACTIVE=false
    NATIVE_LLM_BASE_URL_VALUE=""
    NATIVE_LLM_CONTAINER_BASE_URL_VALUE=""
    NATIVE_LLM_PORT_VALUE=""
    LLAMA_SERVER_API_KEY_VALUE=""
    ODS_HOST_LLM_TRANSPORT_VALUE=direct
    if ods_native_llm_requested; then
        NATIVE_LLM_ACTIVE=true
        NATIVE_LLM_BASE_URL_VALUE="$(ods_native_llm_normalize_origin "$NATIVE_LLM_BASE_URL")" || {
            error "NATIVE_LLM_BASE_URL must be an http://host:port origin."
            return 1
        }
        NATIVE_LLM_CONTAINER_BASE_URL_VALUE="$(ods_native_llm_container_origin "$NATIVE_LLM_BASE_URL_VALUE")"
        NATIVE_LLM_PORT_VALUE="$(ods_native_llm_origin_port "$NATIVE_LLM_BASE_URL_VALUE")"
        # Windows setup supplies the key on every run; a rerun without it keeps
        # the saved key, which the Windows runtime still uses.
        LLAMA_SERVER_API_KEY_VALUE="${LLAMA_SERVER_API_KEY:-$(_env_get LLAMA_SERVER_API_KEY "")}"
        ODS_HOST_LLM_TRANSPORT_VALUE="$(_env_get_explicit_first ODS_HOST_LLM_TRANSPORT direct)"
        case "$ODS_HOST_LLM_TRANSPORT_VALUE" in
            direct|model-router) ;;
            *) error "ODS_HOST_LLM_TRANSPORT must be direct or model-router"; return 1 ;;
        esac
    fi
    ODS_HOST_LLM_TRANSPORT="$ODS_HOST_LLM_TRANSPORT_VALUE"
    # AMD llama.cpp image backend chosen in phase 02 (vulkan by default).
    AMD_INFERENCE_BACKEND_VALUE="${AMD_INFERENCE_BACKEND:-vulkan}"
    AMD_SUPPORTED_BACKENDS_VALUE="vulkan,rocm"
    if ods_amd_target_is_cdna "${AMD_GFX_TARGET:-}"; then
        AMD_SUPPORTED_BACKENDS_VALUE="rocm"
    fi
    LIVEKIT_SECRET=$(_env_get LIVEKIT_API_SECRET "$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)")
    LIVEKIT_API_KEY=$(_phase06_env_hex_secret LIVEKIT_API_KEY 16)
    DASHBOARD_API_KEY=$(_phase06_env_hex_secret DASHBOARD_API_KEY 32)
    ODS_AGENT_KEY=$(_phase06_env_hex_secret ODS_AGENT_KEY 32)
    ODS_AGENT_BIND_VALUE="$(_env_get ODS_AGENT_BIND "${ODS_AGENT_BIND:-}")"
    ODS_AGENT_HOST_VALUE="$(_env_get ODS_AGENT_HOST "${ODS_AGENT_HOST:-}")"
    ODS_AGENT_ADDRESS_MODE_VALUE="$(_env_get ODS_AGENT_ADDRESS_MODE "${ODS_AGENT_ADDRESS_MODE:-}")"
    # HMAC key for signing ods-session cookies (magic-link redemption).
    # 32 random bytes hex-encoded. Rotating invalidates every issued cookie —
    # the only revocation mechanism we have today, so don't rotate casually.
    ODS_SESSION_SECRET=$(_phase06_env_hex_secret ODS_SESSION_SECRET 32)
    # Upstream Hermes otherwise generates this token at process start. Keep it
    # stable so an already-open dashboard can reconnect after a container
    # restart instead of receiving a bare WebSocket 403.
    HERMES_DASHBOARD_SESSION_TOKEN=$(_phase06_env_hex_secret HERMES_DASHBOARD_SESSION_TOKEN 32)
    SHIELD_API_KEY=$(_phase06_env_hex_secret SHIELD_API_KEY 32)
    DIFY_SECRET_KEY=$(_phase06_env_hex_secret DIFY_SECRET_KEY 32)
    QDRANT_API_KEY=$(_phase06_env_hex_secret QDRANT_API_KEY 32)
    _token_spy_key_default=""
    if [[ -f "$INSTALL_DIR/data/token-spy/token-spy-api-key.txt" ]]; then
        _token_spy_key_default=$(tr -d '\r\n' < "$INSTALL_DIR/data/token-spy/token-spy-api-key.txt" 2>/dev/null || true)
    fi
    if [[ -z "$_token_spy_key_default" ]]; then
        _token_spy_key_default=$(_phase06_generate_hex_secret 32)
    fi
    TOKEN_SPY_API_KEY=$(_env_get TOKEN_SPY_API_KEY "$_token_spy_key_default")
    unset _token_spy_key_default
    OPENCODE_SERVER_PASSWORD=$(_env_get OPENCODE_SERVER_PASSWORD "$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64)")
    SEARXNG_SECRET=$(_phase06_env_hex_secret SEARXNG_SECRET 32)

    PIXEL_OPENWEBUI_KEY_VALUE=""
    PIXEL_MODEL_RELAY_KEY_VALUE=""
    PIXEL_MODEL_RELAY_PORT_VALUE=""
    PIXEL_INGRESS_GID_VALUE=""
    PIXEL_SOURCE_URL_VALUE=""
    PIXEL_SOURCE_REF_VALUE=""
    PIXEL_SOURCE_DIR_VALUE=""
    PIXEL_GATEWAY_PORT_VALUE=""
    PIXEL_PREVIEW_PORT_VALUE=""
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" ]]; then
        PIXEL_OPENWEBUI_KEY_VALUE="$(_env_get PIXEL_OPENWEBUI_KEY "")"
        if [[ -z "$PIXEL_OPENWEBUI_KEY_VALUE" ]]; then
            PIXEL_OPENWEBUI_KEY_VALUE="$(ods_pixel_generate_key)" || error "Could not generate Pixel edge key"
        fi
        [[ "$PIXEL_OPENWEBUI_KEY_VALUE" =~ ^[0-9a-f]{64}$ ]] || error "Existing PIXEL_OPENWEBUI_KEY is invalid"
        PIXEL_MODEL_RELAY_KEY_VALUE="$(_env_get PIXEL_MODEL_RELAY_KEY "")"
        if [[ -z "$PIXEL_MODEL_RELAY_KEY_VALUE" ]]; then
            PIXEL_MODEL_RELAY_KEY_VALUE="$(ods_pixel_generate_key)" || error "Could not generate Pixel model relay key"
        fi
        [[ "$PIXEL_MODEL_RELAY_KEY_VALUE" =~ ^[0-9a-f]{64}$ ]] || error "Existing PIXEL_MODEL_RELAY_KEY is invalid"
        PIXEL_MODEL_RELAY_PORT_VALUE="$(_env_get_explicit_first PIXEL_MODEL_RELAY_PORT "4006")"
        [[ "$PIXEL_MODEL_RELAY_PORT_VALUE" =~ ^[1-9][0-9]{0,4}$ \
            && "$PIXEL_MODEL_RELAY_PORT_VALUE" -le 65535 ]] || \
            error "PIXEL_MODEL_RELAY_PORT must be an integer from 1 to 65535"

        # Phase 11 creates/resolves ods-pixel immediately before Compose
        # validation, then atomically fills this initially empty numeric GID.
        PIXEL_INGRESS_GID_VALUE="$(_env_get_preserve_empty PIXEL_INGRESS_GID "")"
        [[ -z "$PIXEL_INGRESS_GID_VALUE" || "$PIXEL_INGRESS_GID_VALUE" =~ ^[1-9][0-9]*$ ]] || \
            error "Existing PIXEL_INGRESS_GID is invalid"

        PIXEL_SOURCE_URL_VALUE="$_phase06_requested_pixel_url"
        PIXEL_SOURCE_REF_VALUE="$_phase06_requested_pixel_ref"
        PIXEL_GATEWAY_PORT_VALUE="$(_env_get_explicit_first PIXEL_GATEWAY_PORT "18789")"
        PIXEL_PREVIEW_PORT_VALUE="$(_env_get_explicit_first PIXEL_PREVIEW_PORT "9437")"
        [[ "$PIXEL_GATEWAY_PORT_VALUE" =~ ^[1-9][0-9]{0,4}$ \
            && "$PIXEL_PREVIEW_PORT_VALUE" =~ ^[1-9][0-9]{0,4}$ \
            && "$PIXEL_GATEWAY_PORT_VALUE" -le 65535 \
            && "$PIXEL_PREVIEW_PORT_VALUE" -le 65535 ]] || \
            error "Pixel gateway and preview ports must be integers from 1 to 65535"
        [[ "$PIXEL_GATEWAY_PORT_VALUE" != "$PIXEL_PREVIEW_PORT_VALUE" ]] || \
            error "PIXEL_GATEWAY_PORT and PIXEL_PREVIEW_PORT must be different"
        [[ "$PIXEL_MODEL_RELAY_PORT_VALUE" != "$PIXEL_GATEWAY_PORT_VALUE" \
            && "$PIXEL_MODEL_RELAY_PORT_VALUE" != "$PIXEL_PREVIEW_PORT_VALUE" \
            && "$PIXEL_MODEL_RELAY_PORT_VALUE" != "${LITELLM_PORT:-4000}" ]] || \
            error "Pixel model relay port conflicts with another Pixel or LiteLLM port"
        export PIXEL_MODEL_RELAY_PORT="$PIXEL_MODEL_RELAY_PORT_VALUE"
        export PIXEL_MODEL_RELAY_KEY="$PIXEL_MODEL_RELAY_KEY_VALUE"
        export PIXEL_GATEWAY_PORT="$PIXEL_GATEWAY_PORT_VALUE"
        export PIXEL_PREVIEW_PORT="$PIXEL_PREVIEW_PORT_VALUE"
        PIXEL_WEB_SEARCH_PROVIDER_VALUE="$(_env_get_explicit_first PIXEL_WEB_SEARCH_PROVIDER "")"
        case "$PIXEL_WEB_SEARCH_PROVIDER_VALUE" in
            ""|parallel-free|searxng) ;;
            *) ai_bad "PIXEL_WEB_SEARCH_PROVIDER must be parallel-free or searxng."; return 1 ;;
        esac
        export PIXEL_WEB_SEARCH_PROVIDER="$PIXEL_WEB_SEARCH_PROVIDER_VALUE"
        PIXEL_SOURCE_DIR_VALUE="$_phase06_requested_pixel_dir"
        # Phase 11 installs Pixel in this same installer shell. Preserve the
        # resolved immutable source contract in that shell as well as in .env;
        # transient environment prefixes used for validation do not persist.
        ods_pixel_activate_source_contract \
            "$PIXEL_SOURCE_URL_VALUE" "$PIXEL_SOURCE_REF_VALUE" "$PIXEL_SOURCE_DIR_VALUE" || \
            error "Pixel source URL/ref failed the immutable-source policy"

        # Prove the immutable source is obtainable before downloading images or
        # building the ODS stack. A clean machine must not spend several minutes
        # and gigabytes only to discover at the launch boundary that its Pixel
        # source needs credentials or is otherwise unavailable. Phase 11
        # independently revalidates this exact clean checkout before activation.
        _phase06_step "preflight-pixel-source"
        _phase06_pixel_owner="$(ods_pixel_install_owner)" || \
            error "Could not identify the ODS owner for Pixel source preflight"
        _phase06_pixel_home="$(ods_pixel_owner_home "$_phase06_pixel_owner")" || \
            error "Could not resolve the ODS owner home for Pixel source preflight"
        _phase06_pixel_source_root="$INSTALL_DIR/data/pixel/source-$PIXEL_SOURCE_REF_VALUE"
        if ! _ods_pixel_source_checkout \
            "$_phase06_pixel_owner" "$_phase06_pixel_home" "$_phase06_pixel_source_root" >/dev/null; then
            error "Pixel source is unavailable. Verify the bundled source and its digest, or use a documented clean local developer checkout before retrying."
        fi
        unset _phase06_pixel_owner _phase06_pixel_home _phase06_pixel_source_root
    fi

    # Langfuse (LLM Observability). LANGFUSE_ENABLED mirrors the install-time
    # ENABLE_LANGFUSE toggle, falling back to whatever the user had in .env on
    # re-install so manual post-install `ods enable langfuse` edits survive.
    LANGFUSE_PORT=$(_env_get LANGFUSE_PORT "3006")
    LANGFUSE_ENABLED=$(_env_get LANGFUSE_ENABLED "${ENABLE_LANGFUSE:-false}")
    LANGFUSE_NEXTAUTH_SECRET=$(_phase06_env_hex_secret LANGFUSE_NEXTAUTH_SECRET 32)
    LANGFUSE_SALT=$(_phase06_env_hex_secret LANGFUSE_SALT 32)
    LANGFUSE_ENCRYPTION_KEY=$(_phase06_env_hex_secret LANGFUSE_ENCRYPTION_KEY 32)
    LANGFUSE_DB_PASSWORD=$(_phase06_env_hex_secret LANGFUSE_DB_PASSWORD 16)
    LANGFUSE_CLICKHOUSE_PASSWORD=$(_phase06_env_hex_secret LANGFUSE_CLICKHOUSE_PASSWORD 16)
    LANGFUSE_REDIS_PASSWORD=$(_phase06_env_hex_secret LANGFUSE_REDIS_PASSWORD 16)
    LANGFUSE_MINIO_ACCESS_KEY=$(_phase06_env_hex_secret LANGFUSE_MINIO_ACCESS_KEY 16)
    LANGFUSE_MINIO_SECRET_KEY=$(_phase06_env_hex_secret LANGFUSE_MINIO_SECRET_KEY 32)
    LANGFUSE_PROJECT_PUBLIC_KEY=$(_phase06_env_hex_secret LANGFUSE_PROJECT_PUBLIC_KEY 16 "pk-lf-ods-")
    LANGFUSE_PROJECT_SECRET_KEY=$(_phase06_env_hex_secret LANGFUSE_PROJECT_SECRET_KEY 16 "sk-lf-ods-")
    LANGFUSE_INIT_PROJECT_ID=$(_phase06_env_hex_secret LANGFUSE_INIT_PROJECT_ID 16)
    LANGFUSE_INIT_USER_EMAIL=$(_env_get LANGFUSE_INIT_USER_EMAIL "admin@ods.local")
    LANGFUSE_INIT_USER_PASSWORD=$(_phase06_env_hex_secret LANGFUSE_INIT_USER_PASSWORD 16)
    MODEL_PROFILE_VALUE=$(_env_get MODEL_PROFILE "${MODEL_PROFILE_REQUESTED:-${MODEL_PROFILE:-qwen}}")
    MODEL_RECOMMENDED_MODEL_VALUE="${INSTALLER_RECOMMENDED_MODEL:-${LLM_MODEL}}"
    MODEL_RECOMMENDED_GGUF_VALUE="${INSTALLER_RECOMMENDED_GGUF:-${GGUF_FILE}}"
    MODEL_RECOMMENDED_CONTEXT_VALUE="${INSTALLER_RECOMMENDED_CONTEXT:-${MAX_CONTEXT}}"
    MODEL_SELECTION_SOURCE_VALUE="${MODEL_SELECTION_SOURCE:-installer}"
    EXTERNAL_LLM_URL_VALUE="${EXTERNAL_LLM_URL:-}"
    EXTERNAL_LLM_CONTAINER_URL_VALUE="${EXTERNAL_LLM_CONTAINER_URL:-}"
    EXTERNAL_LLM_PROVIDER_VALUE="${EXTERNAL_LLM_PROVIDER:-}"
    EXTERNAL_SELECTED_MODEL="${EXTERNAL_LLM_MODEL:-}"
    EXTERNAL_LLM_ACTIVE=false
    if [[ -n "$EXTERNAL_LLM_URL_VALUE" ]]; then
        if [[ -z "$EXTERNAL_LLM_CONTAINER_URL_VALUE" || -z "$EXTERNAL_LLM_PROVIDER_VALUE" || -z "$EXTERNAL_SELECTED_MODEL" ]]; then
            error "External LLM selection is incomplete. Re-run with a reachable endpoint and model, or use --no-external-llm."
        fi
        EXTERNAL_LLM_ACTIVE=true
        LLM_MODEL="$EXTERNAL_SELECTED_MODEL"
        _external_key_target="$INSTALL_DIR/config/litellm/external-upstream.key"
        [[ ! -L "$_external_key_target" && ( ! -e "$_external_key_target" || -f "$_external_key_target" ) ]] || {
            error "External LLM key destination must be a regular file."
            return 1
        }
        if [[ -n "${EXTERNAL_LLM_API_KEY_FILE:-}" ]]; then
            external_llm_read_api_key "$EXTERNAL_LLM_API_KEY_FILE" >/dev/null || return 1
            if [[ "$EXTERNAL_LLM_API_KEY_FILE" != "$_external_key_target" ]]; then
                _external_key_tmp="$(mktemp "${_external_key_target}.XXXXXX")" || return 1
                chmod 600 "$_external_key_tmp"
                if ! external_llm_read_api_key "$EXTERNAL_LLM_API_KEY_FILE" >"$_external_key_tmp"; then
                    rm -f -- "$_external_key_tmp"
                    error "Could not stage the external LLM key."
                    return 1
                fi
                mv -f -- "$_external_key_tmp" "$_external_key_target"
            fi
        elif [[ -n "${EXTERNAL_LLM_API_KEY_VALUE:-}" ]]; then
            # A key from --external-llm-key-env (Windows setup) or the
            # retired --lemonade-api-key flag.
            _external_key_tmp="$(mktemp "${_external_key_target}.XXXXXX")" || return 1
            chmod 600 "$_external_key_tmp"
            printf '%s\n' "$EXTERNAL_LLM_API_KEY_VALUE" >"$_external_key_tmp"
            mv -f -- "$_external_key_tmp" "$_external_key_target"
            unset EXTERNAL_LLM_API_KEY_VALUE
        elif [[ "${EXTERNAL_LLM_API_KEY_RESET:-false}" == "true" ]]; then
            (umask 077; : >"$_external_key_target")
        elif [[ ! -e "$_external_key_target" ]]; then
            (umask 077; : >"$_external_key_target")
        fi
        chmod 600 "$_external_key_target"
        if [[ -s "$_external_key_target" ]]; then
            EXTERNAL_LLM_API_KEY_FILE="$_external_key_target"
        fi
        unset _external_key_tmp _external_key_target
    elif [[ "${EXTERNAL_LLM_RESET:-false}" == "true" ]]; then
        # --no-external-llm turns API mode off and forgets its key (fleet row
        # 25). The overlay that mounted the key leaves the stack with it, and
        # a later API setup stores a key again.
        _external_key_target="$INSTALL_DIR/config/litellm/external-upstream.key"
        if [[ -f "$_external_key_target" || -L "$_external_key_target" ]]; then
            rm -f -- "$_external_key_target"
            log "Removed the stored external LLM key (API mode is off)"
        fi
        unset _external_key_target
    fi
    # The AMD overlays pin their own llama.cpp images. A model profile's image
    # for another backend (the gemma4 profile names the CUDA build) must not
    # reach .env, where the host agent's container recreate would use it.
    if [[ "$GPU_BACKEND" == "amd" ]]; then
        _amd_llama_repo="ghcr.io/ggml-org/llama.cpp"
        for _amd_image_key in LLAMA_SERVER_IMAGE LLAMA_SERVER_IMAGE_FALLBACK; do
            case "${!_amd_image_key:-}" in
                ""|"$_amd_llama_repo":server-vulkan-*|"$_amd_llama_repo":server-rocm-*) ;;
                *)
                    log "Not writing $_amd_image_key for AMD: ${!_amd_image_key} is another backend's image"
                    unset "$_amd_image_key"
                    ;;
            esac
        done
        unset _amd_image_key _amd_llama_repo
    fi
    LLAMA_SERVER_MEMORY_LIMIT_VALUE=""
    if [[ "$GPU_BACKEND" == "nvidia" && "$EXTERNAL_LLM_ACTIVE" != "true" && "${ODS_MODE:-local}" != "cloud" ]]; then
        _docker_memory_gb="$(ods_docker_memory_gb 2>/dev/null || true)"
        _effective_memory_gb="$(ods_effective_container_memory_gb "${RAM_GB:-0}" "$_docker_memory_gb")"
        _llama_memory_default="$(ods_default_nvidia_llama_memory_limit "$_effective_memory_gb")"
        LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "${LLAMA_SERVER_MEMORY_LIMIT:-$_llama_memory_default}")"
        unset _docker_memory_gb _effective_memory_gb _llama_memory_default
    elif [[ "$GPU_BACKEND" == "cpu" || "$GPU_BACKEND" == "none" ]] \
        && [[ "$EXTERNAL_LLM_ACTIVE" != "true" && "${ODS_MODE:-local}" != "cloud" ]]; then
        # CPU runtime profiles size the container for their model (weights,
        # KV and capped context checkpoints). Without one, leave the key unset
        # so docker-compose.cpu.yml's 6G default applies as before.
        LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "${LLAMA_SERVER_MEMORY_LIMIT:-}")"
    fi
    ODS_MODE_VALUE="$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" || "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "local"; else echo "${ODS_MODE:-local}"; fi)"
    # The managed AMD container and the host-native route are both local mode.
    AMD_LOCAL_RUNTIME=false
    if [[ "$EXTERNAL_LLM_ACTIVE" != "true" && "$NATIVE_LLM_ACTIVE" != "true" \
        && "$GPU_BACKEND" == "amd" && "$ODS_MODE_VALUE" == "local" ]]; then
        AMD_LOCAL_RUNTIME=true
    fi
    ODS_MODEL_SWITCHBOARD_VALUE=$(_env_get ODS_MODEL_SWITCHBOARD "${ODS_MODEL_SWITCHBOARD:-enabled}")
    case "$ODS_MODEL_SWITCHBOARD_VALUE" in
        legacy|observe|enabled) ;;
        *) ODS_MODEL_SWITCHBOARD_VALUE="enabled" ;;
    esac
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" && "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" ]]; then
        ai_warn "External LLM reuse uses the authenticated LiteLLM gateway directly; setting ODS_MODEL_SWITCHBOARD=observe."
        ODS_MODEL_SWITCHBOARD_VALUE="observe"
    fi
    # Compose inherits exported installer variables ahead of the generated
    # .env. Keep the live process value aligned with the effective value so an
    # external-model install cannot select switchboard.yaml while its router
    # service is disabled by docker-compose.external-llm.yml.
    ODS_MODEL_SWITCHBOARD="$ODS_MODEL_SWITCHBOARD_VALUE"
    export ODS_MODEL_SWITCHBOARD
    # A host-native llama-server is reachable only through LiteLLM, which holds
    # its key; the in-stack llama-server serves every other local install.
    _default_llm_api_url="$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "http://litellm:4000"; elif [[ "${ODS_MODE:-local}" == "local" ]]; then echo "http://llama-server:8080"; else echo "http://litellm:4000"; fi)"
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then
        LLM_API_URL_VALUE="http://litellm:4000"
        OPEN_WEBUI_LLM_BASE_URL_VALUE="http://litellm:4000/v1"
        OPEN_WEBUI_LLM_API_KEY_VALUE="${LITELLM_KEY}"
    elif [[ "${EXTERNAL_LLM_RESET:-false}" == "true" ]]; then
        LLM_API_URL_VALUE="$_default_llm_api_url"
        OPEN_WEBUI_LLM_BASE_URL_VALUE=""
        OPEN_WEBUI_LLM_API_KEY_VALUE=""
    else
        LLM_API_URL_VALUE=$(_env_get LLM_API_URL "$_default_llm_api_url")
        # A paused model API (Settings > Remote model) had pointed this at
        # LiteLLM; restore the URL it replaced (install-core read it).
        if [[ "${ODS_REMOTE_ROUTE_PAUSED:-false}" == "true" ]]; then
            LLM_API_URL_VALUE="${ODS_REMOTE_ROUTE_PREVIOUS_API_URL:-$_default_llm_api_url}"
        fi
        # The in-stack llama-server is off for a host-native route. Preserve
        # other existing values as operator-selected endpoints.
        if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then
            case "$LLM_API_URL_VALUE" in
                http://llama-server:8080|http://llama-server:8080/v1)
                    LLM_API_URL_VALUE="$_default_llm_api_url"
                    ;;
            esac
        fi
    fi
    if [[ "$EXTERNAL_LLM_ACTIVE" != "true" && "${EXTERNAL_LLM_RESET:-false}" != "true" && "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" ]]; then
        OPEN_WEBUI_LLM_BASE_URL_VALUE=$(_env_get OPEN_WEBUI_LLM_BASE_URL "http://litellm:4000")
        OPEN_WEBUI_LLM_API_KEY_VALUE=$(_env_get OPEN_WEBUI_LLM_API_KEY "${LITELLM_KEY}")
    elif [[ "$EXTERNAL_LLM_ACTIVE" != "true" && "${EXTERNAL_LLM_RESET:-false}" != "true" ]]; then
        OPEN_WEBUI_LLM_BASE_URL_VALUE=$(_env_get OPEN_WEBUI_LLM_BASE_URL "")
        OPEN_WEBUI_LLM_API_KEY_VALUE=$(_env_get OPEN_WEBUI_LLM_API_KEY "")
    fi
    _default_open_webui_task_model=""
    if [[ "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" ]]; then
        _default_open_webui_task_model="ods/current"
    elif [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then
        _default_open_webui_task_model="ods/current"
    elif [[ "$OPEN_WEBUI_LLM_BASE_URL_VALUE" == *"litellm:4000"* || "$LLM_API_URL_VALUE" == *"litellm:4000"* ]]; then
        _default_open_webui_task_model="default"
    fi
    # Direct llama.cpp deliberately leaves this empty: the Pixel Compose
    # overlay then follows GGUF_FILE, which is the exact /v1/models ID and
    # changes with bootstrap promotion. LLM_MODEL is only a logical catalog ID.
    OPEN_WEBUI_TASK_MODEL_VALUE=$(_env_get OPEN_WEBUI_TASK_MODEL "$_default_open_webui_task_model")
    if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
    elif [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
    else
        _default_hermes_base_url="http://llama-server:8080/v1"
        _default_hermes_api_key="sk-ods-hermes-local"
    fi
    if [[ "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" && "${ODS_MODE:-local}" != "cloud" && "$EXTERNAL_LLM_ACTIVE" != "true" ]]; then
        # Local Hermes streams directly through model-router so an abandoned
        # Talk request can cancel the active backend request.  LiteLLM remains
        # the authenticated cloud/external gateway, but its retry layer can
        # outlive a disconnected local Hermes client and pin the only slot.
        _default_hermes_base_url="http://model-router:9099/v1"
        _default_hermes_api_key="no-key"
    fi
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then
        HERMES_LLM_BASE_URL_VALUE="http://litellm:4000/v1"
        HERMES_LLM_API_KEY_VALUE="${LITELLM_KEY}"
    elif [[ "${EXTERNAL_LLM_RESET:-false}" == "true" ]]; then
        HERMES_LLM_BASE_URL_VALUE="$_default_hermes_base_url"
        HERMES_LLM_API_KEY_VALUE="$_default_hermes_api_key"
    else
        HERMES_LLM_BASE_URL_VALUE=$(_env_get HERMES_LLM_BASE_URL "$_default_hermes_base_url")
        HERMES_LLM_API_KEY_VALUE=$(_env_get HERMES_LLM_API_KEY "$_default_hermes_api_key")
    fi
    if [[ "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" && "${ODS_MODE:-local}" != "cloud" && "$EXTERNAL_LLM_ACTIVE" != "true" && "$HERMES_LLM_BASE_URL_VALUE" == "http://litellm:4000/v1" ]]; then
        # Migrate the former managed default on upgrade.  Preserve every
        # non-default custom endpoint exactly as supplied by the operator.
        HERMES_LLM_BASE_URL_VALUE="http://model-router:9099/v1"
        HERMES_LLM_API_KEY_VALUE="no-key"
    fi
    LLM_API_URL="$LLM_API_URL_VALUE"
    HERMES_LLM_BASE_URL="$HERMES_LLM_BASE_URL_VALUE"
    HERMES_LLM_API_KEY="$HERMES_LLM_API_KEY_VALUE"

    _select_auto_cpu_value() {
        local key="$1" detected="$2"
        local existing
        existing=$(_env_get "$key" "")
        if [[ "$existing" =~ ^[0-9]+([.][0-9]+)?$ ]] && LC_ALL=C awk "BEGIN { exit !($existing > 0 && $existing <= $detected) }"; then
            echo "$existing"
        else
            echo "$detected"
        fi
    }

    _cap_cpu_value() {
        local desired="$1" ceiling="$2"
        LC_ALL=C awk -v desired="$desired" -v ceiling="$ceiling" '
            BEGIN {
                if (ceiling <= 0) ceiling = 1
                value = desired
                if (value > ceiling) value = ceiling
                if (value < 0.01) value = 0.01
                printf "%.1f", value
            }'
    }

    _select_service_cpu_limit() {
        local key="$1" desired="$2" available="$3"
        _select_auto_cpu_value "$key" "$(_cap_cpu_value "$desired" "$available")"
    }

    _select_service_cpu_reservation() {
        local key="$1" desired="$2" limit="$3"
        _select_auto_cpu_value "$key" "$(_cap_cpu_value "$desired" "$limit")"
    }

    _cpu_backend="${GPU_BACKEND:-cpu}"
    [[ "$_cpu_backend" == "none" ]] && _cpu_backend="cpu"
    read -r _llama_cpu_limit_raw _llama_cpu_reservation_raw _docker_available_cpus <<< "$(calculate_llama_cpu_budget "$_cpu_backend")"
    _llama_cpu_limit_detected="${_llama_cpu_limit_raw}.0"
    _llama_cpu_reservation_detected="${_llama_cpu_reservation_raw}.0"
    LLAMA_CPU_LIMIT=$(_select_auto_cpu_value LLAMA_CPU_LIMIT "${_llama_cpu_limit_detected}")
    LLAMA_CPU_RESERVATION=$(_select_auto_cpu_value LLAMA_CPU_RESERVATION "${_llama_cpu_reservation_detected}")
    if LC_ALL=C awk "BEGIN { exit !($LLAMA_CPU_RESERVATION > $LLAMA_CPU_LIMIT) }"; then
        LLAMA_CPU_RESERVATION="$LLAMA_CPU_LIMIT"
    fi
    # CPU inference: llama.cpp's own default is one thread per physical core;
    # the compose file's fixed 4 left most cores idle. Bound it by the
    # container's CPU limit. An owner-set LLAMA_THREADS is kept. GPU backends
    # keep the compose default (their threads only feed the GPU).
    LLAMA_THREADS_VALUE=""
    if [[ "$_cpu_backend" == "cpu" && "${ODS_MODE:-local}" != "cloud" ]]; then
        LLAMA_THREADS_VALUE="$(_env_get LLAMA_THREADS \
            "$(ods_default_cpu_llama_threads "$(ods_physical_cpu_cores 2>/dev/null || true)" "$LLAMA_CPU_LIMIT")")"
        [[ "$LLAMA_THREADS_VALUE" =~ ^[1-9][0-9]*$ ]] || LLAMA_THREADS_VALUE=""
    fi

    TTS_WORKERS_VALUE="$(_env_get TTS_WORKERS "${TTS_WORKERS:-1}")"
    if [[ ! "$TTS_WORKERS_VALUE" =~ ^[1-9][0-9]*$ ]]; then
        TTS_WORKERS_VALUE=1
    fi

    TTS_CPU_LIMIT=$(_select_service_cpu_limit TTS_CPU_LIMIT "8.0" "$_docker_available_cpus")
    TTS_CPU_RESERVATION=$(_select_service_cpu_reservation TTS_CPU_RESERVATION "2.0" "$TTS_CPU_LIMIT")
    TTS_THREADS_VALUE="$(ods_select_tts_threads "$(_env_get TTS_THREADS "${TTS_THREADS:-}")" "$TTS_CPU_LIMIT" "$TTS_WORKERS_VALUE")"
    WHISPER_CPU_LIMIT=$(_select_service_cpu_limit WHISPER_CPU_LIMIT "4.0" "$_docker_available_cpus")
    WHISPER_CPU_RESERVATION=$(_select_service_cpu_reservation WHISPER_CPU_RESERVATION "1.0" "$WHISPER_CPU_LIMIT")
    HERMES_CPU_LIMIT=$(_select_service_cpu_limit HERMES_CPU_LIMIT "4.0" "$_docker_available_cpus")
    HERMES_CPU_RESERVATION=$(_select_service_cpu_reservation HERMES_CPU_RESERVATION "0.5" "$HERMES_CPU_LIMIT")
    COMFYUI_CPU_LIMIT=$(_select_service_cpu_limit COMFYUI_CPU_LIMIT "16.0" "$_docker_available_cpus")
    COMFYUI_CPU_RESERVATION=$(_select_service_cpu_reservation COMFYUI_CPU_RESERVATION "2.0" "$COMFYUI_CPU_LIMIT")

    # Network binding (--lan or exported BIND_ADDRESS wins over a stale .env;
    # otherwise preserve the existing .env value and default to localhost-only).
    if [[ "${BIND_ADDRESS_EXPLICIT:-false}" == "true" && -n "${BIND_ADDRESS:-}" ]]; then
        BIND_ADDRESS="${BIND_ADDRESS}"
    else
        BIND_ADDRESS=$(_env_get BIND_ADDRESS "${BIND_ADDRESS:-127.0.0.1}")
    fi
    if [[ "${ENABLE_ODS_PROXY:-false}" == "true" ]] \
        || [[ "$BIND_ADDRESS" != "127.0.0.1" && "$BIND_ADDRESS" != "::1" && "$BIND_ADDRESS" != "localhost" ]]; then
        # Never carry an authless localhost value into a network-exposed rerun.
        WEBUI_AUTH="true"
    else
        # On loopback, preserve an operator's explicit opt-in to authentication.
        WEBUI_AUTH=$(_env_get WEBUI_AUTH "false")
    fi

    # Device name — used by ods-mdns (publishes <name>.local + per-service
    # subdomains: auth.<name>.local, chat.<name>.local, etc.) and by magic-
    # link URL generation in dashboard-api. The previous default literal
    # "ods" causes mDNS NonUniqueNameException collisions when more than
    # one ODS install is on the same LAN: the first one wins and
    # every subsequent host's mDNS service crash-loops, so phones following
    # invite QR codes from the losing hosts land on the winning host (or
    # nothing at all). Auto-derive from the system hostname for per-host
    # uniqueness, sanitized to match .env.schema.json's pattern
    # (^[a-zA-Z0-9][a-zA-Z0-9-]{0,30}[a-zA-Z0-9]$|^[a-zA-Z0-9]$). Fall back
    # to "ods" only when the hostname can't be sanitized into the schema.
    _device_default="ods"
    if command -v hostname >/dev/null 2>&1; then
        _raw_hn="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
        # Lowercase; collapse any non-[a-z0-9-] to a single '-'; trim
        # leading/trailing '-'; cap at 32 chars.
        _hn="$(printf '%s' "$_raw_hn" \
            | tr '[:upper:]' '[:lower:]' \
            | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//' \
            | cut -c1-32 \
            | sed -E 's/-+$//')"
        # Schema requires first + last char alphanumeric.
        if [[ -n "$_hn" && "$_hn" =~ ^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$ ]]; then
            _device_default="$_hn"
        fi
    fi
    ODS_DEVICE_NAME=$(_env_get ODS_DEVICE_NAME "$_device_default")

    # Whisper acceleration is separately capability-gated from the primary LLM
    # backend because the pinned Speaches CUDA image has a stricter driver
    # floor. Phase 02 forces CPU only for Whisper when needed.
    if [[ "${WHISPER_ACCELERATION_FORCED_CPU:-false}" == "true" ]]; then
        WHISPER_ACCELERATION_VALUE="cpu"
    else
        WHISPER_ACCELERATION_VALUE=$(_env_get WHISPER_ACCELERATION "${WHISPER_ACCELERATION:-$([[ "$GPU_BACKEND" == "nvidia" ]] && echo cuda || echo cpu)}")
    fi
    case "$WHISPER_ACCELERATION_VALUE" in
        cpu|cuda) ;;
        *) WHISPER_ACCELERATION_VALUE="$([[ "$GPU_BACKEND" == "nvidia" ]] && echo cuda || echo cpu)" ;;
    esac
    if [[ "$WHISPER_ACCELERATION_VALUE" == "cuda" ]]; then
        _default_stt_model="deepdml/faster-whisper-large-v3-turbo-ct2"
        _default_whisper_image=""
    else
        _default_stt_model="Systran/faster-whisper-base"
        _default_whisper_image="ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu@sha256:2163775b6df5e451a71200e8f675fed68dbd8ab184fc604453d549e486f22fd2"
    fi
    AUDIO_STT_MODEL=$(_env_get AUDIO_STT_MODEL "${AUDIO_STT_MODEL:-$_default_stt_model}")
    WHISPER_IMAGE_VALUE=$(_env_get WHISPER_IMAGE "${WHISPER_IMAGE:-$_default_whisper_image}")
    if [[ "$WHISPER_ACCELERATION_VALUE" == "cpu" ]]; then
        [[ "$AUDIO_STT_MODEL" =~ ([Ll]arge-v3|[Tt]urbo) ]] && AUDIO_STT_MODEL="$_default_stt_model"
        if [[ -z "$WHISPER_IMAGE_VALUE" || "$WHISPER_IMAGE_VALUE" =~ [Cc][Uu][Dd][Aa] ]]; then
            WHISPER_IMAGE_VALUE="$_default_whisper_image"
        fi
    fi
    EMBEDDING_MODEL_VALUE=$(_env_get EMBEDDING_MODEL "${EMBEDDING_MODEL:-BAAI/bge-base-en-v1.5}")
    RAG_EMBEDDING_MODEL_VALUE=$(_env_get_preserve_empty RAG_EMBEDDING_MODEL "${RAG_EMBEDDING_MODEL:-}")
    RAG_OPENAI_API_BASE_URL_VALUE=$(_env_get_preserve_empty RAG_OPENAI_API_BASE_URL "${RAG_OPENAI_API_BASE_URL:-}")
    RAG_OPENAI_API_KEY_VALUE=$(_env_get_preserve_empty RAG_OPENAI_API_KEY "${RAG_OPENAI_API_KEY:-}")
    EMBEDDINGS_MEMORY_LIMIT_VALUE=$(_env_get EMBEDDINGS_MEMORY_LIMIT "${EMBEDDINGS_MEMORY_LIMIT:-4G}")
    N_GPU_LAYERS_VALUE=$(_env_get N_GPU_LAYERS "${N_GPU_LAYERS:-auto}")
    N_GPU_LAYERS_VALUE="$(printf '%s' "$N_GPU_LAYERS_VALUE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    N_GPU_LAYERS_VALUE="${N_GPU_LAYERS_VALUE:-auto}"
    # Owner opt-out for the overlay default; empty keeps ngram-mod implicit.
    LLAMA_SPEC_TYPE_VALUE=$(_env_get LLAMA_SPEC_TYPE "${LLAMA_SPEC_TYPE:-}")

    # A retained port is kept (an AMD install moved to 9100 stays there).
    WHISPER_PORT_VALUE="$(_env_get_explicit_first WHISPER_PORT "9000")"
    WHISPER_PORT="$WHISPER_PORT_VALUE"
    if declare -p SERVICE_PORTS >/dev/null 2>&1; then
        SERVICE_PORTS[whisper]="$WHISPER_PORT_VALUE"
    fi

    # Preserve user-supplied cloud API keys
    ANTHROPIC_API_KEY=$(_env_get ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}")
    OPENAI_API_KEY=$(_env_get OPENAI_API_KEY "${OPENAI_API_KEY:-}")
    TOGETHER_API_KEY=$(_env_get TOGETHER_API_KEY "${TOGETHER_API_KEY:-}")
    MINIMAX_API_KEY=$(_env_get MINIMAX_API_KEY "${MINIMAX_API_KEY:-}")
    # Base64-encode GPU assignment JSON for safe .env storage
    if [[ -n "${GPU_ASSIGNMENT_JSON:-}" && "${GPU_ASSIGNMENT_JSON:-}" != "{}" ]]; then
        GPU_ASSIGNMENT_JSON_B64=$(echo "$GPU_ASSIGNMENT_JSON" | jq -c '.' | base64 -w0)
    else
        GPU_ASSIGNMENT_JSON_B64=""
    fi
    # Resolve before opening .env for writing; a here-document lookup would
    # read the already-truncated file and lose a retained host port override.
    DASHBOARD_API_PORT_VALUE="$(_env_get DASHBOARD_API_PORT 3002)"
    # Phase 05 renders Pixel's extension-manager unit from this shell value.
    # Keep it aligned with the retained .env port on an upgrade.
    DASHBOARD_API_PORT="$DASHBOARD_API_PORT_VALUE"

    # Generate .env file
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == true ]]; then
        _phase06_pixel_runtime_layout || {
            error "Pixel could not verify the local WSL Docker daemon or Docker Desktop shared runtime mount"
            return 1
        }
    fi
    # Subshell-scope a tighter umask so the file is created 0600 from the start
    # (closes a brief window on systems where $HOME is world-readable, e.g.
    # Ubuntu defaults). The umask MUST NOT leak to the rest of phase 06 or
    # subsequent phases — later mkdirs create container-bind-mount dirs that
    # need world-traverse (e.g. SearXNG runs as uid 977).
    # The chmod 600 below is belt-and-braces.
    # The template only knows ODS's own keys. Snapshot the previous .env so the
    # settings bundled and installed extensions own can be carried over after
    # the rewrite.
    _phase06_previous_env=""
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        _phase06_previous_env="$(mktemp)" || return 1
        cp "$INSTALL_DIR/.env" "$_phase06_previous_env" || return 1
    fi
    (
        umask 077
        cat > "$INSTALL_DIR/.env" << ENV_EOF
# ODS Configuration — ${TIER_NAME} Edition
# Generated by installer v${VERSION} on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Tier: ${TIER} (${TIER_NAME})

#=== ODS Version (used by ods-cli update for version-compat checks) ===
ODS_VERSION=${VERSION:-3.0.0}

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with --lan or set manually)
BIND_ADDRESS=$(dotenv_value "${BIND_ADDRESS}")
DASHBOARD_PORT=${DASHBOARD_PORT_VALUE}
DASHBOARD_REMOTE_PORT=${DASHBOARD_REMOTE_PORT_VALUE}
# Lets the non-root remote-provider services read only lifecycle secrets that
# the host agent writes mode 0640 under this installation owner's data group.
REMOTE_PROVIDER_DATA_GID=$(id -g 2>/dev/null || echo 1000)

#=== LLM Backend Mode ===
ODS_MODE=${ODS_MODE_VALUE}
ODS_GATEWAY_ONLY=${ODS_GATEWAY_ONLY:-false}
ENABLE_OPEN_WEBUI=${ENABLE_OPEN_WEBUI:-true}
ENABLE_DEVTOOLS=${ENABLE_DEVTOOLS:-false}
ODS_MODEL_SWITCHBOARD=$(dotenv_value "${ODS_MODEL_SWITCHBOARD_VALUE}")
LLM_API_URL=$(dotenv_value "${LLM_API_URL_VALUE}")
OPEN_WEBUI_LLM_BASE_URL=$(dotenv_value "${OPEN_WEBUI_LLM_BASE_URL_VALUE}")
OPEN_WEBUI_LLM_API_KEY=$(dotenv_value "${OPEN_WEBUI_LLM_API_KEY_VALUE}")
OPEN_WEBUI_TASK_MODEL=$(dotenv_value "${OPEN_WEBUI_TASK_MODEL_VALUE}")
LLM_BACKEND=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo "external"; else echo "llama-server"; fi)
LLM_API_BASE_PATH=/v1
EXTERNAL_LLM_URL=${EXTERNAL_LLM_URL_VALUE}
EXTERNAL_LLM_CONTAINER_URL=${EXTERNAL_LLM_CONTAINER_URL_VALUE}
EXTERNAL_LLM_PROVIDER=${EXTERNAL_LLM_PROVIDER_VALUE}
EXTERNAL_LLM_MODEL=${EXTERNAL_SELECTED_MODEL}
SKIP_MODEL_DOWNLOAD=${EXTERNAL_LLM_ACTIVE}
AMD_INFERENCE_RUNTIME=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" || "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "llama-server"; fi)
AMD_INFERENCE_BACKEND=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "vulkan"; elif [[ "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "${AMD_INFERENCE_BACKEND_VALUE}"; fi)
AMD_INFERENCE_LOCATION=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "host"; elif [[ "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "container"; fi)
AMD_INFERENCE_PORT=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "${NATIVE_LLM_PORT_VALUE}"; elif [[ "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "8080"; fi)
AMD_INFERENCE_SUPPORTED_BACKENDS=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "vulkan"; elif [[ "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "${AMD_SUPPORTED_BACKENDS_VALUE}"; fi)
AMD_INFERENCE_RUNTIME_MODE=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then echo "windows-portal-llama-server"; elif [[ "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "linux-container"; fi)
AMD_INFERENCE_MANAGED=$(if [[ "$NATIVE_LLM_ACTIVE" == "true" || "$AMD_LOCAL_RUNTIME" == "true" ]]; then echo "true"; fi)
ODS_HOST_LLM_TRANSPORT=$(dotenv_value "${ODS_HOST_LLM_TRANSPORT_VALUE}")
NATIVE_LLM_BASE_URL=$(dotenv_value "${NATIVE_LLM_BASE_URL_VALUE}")
NATIVE_LLM_CONTAINER_BASE_URL=$(dotenv_value "${NATIVE_LLM_CONTAINER_BASE_URL_VALUE}")
$(if [[ -n "$LLAMA_SERVER_API_KEY_VALUE" ]]; then printf 'LLAMA_SERVER_API_KEY=%s' "$(dotenv_value "$LLAMA_SERVER_API_KEY_VALUE")"; fi)
$(if [[ -n "${ODS_WINDOWS_SYSTEM_DIRECTORY}" ]]; then printf 'ODS_WINDOWS_SYSTEM_DIRECTORY=%s' "$(dotenv_value "$ODS_WINDOWS_SYSTEM_DIRECTORY")"; fi)
$(if [[ -n "${ODS_WSL_STATE_ROOT}" ]]; then printf 'ODS_WSL_STATE_ROOT=%s' "$(dotenv_value "$ODS_WSL_STATE_ROOT")"; fi)

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=$(dotenv_value "${ANTHROPIC_API_KEY:-}")
OPENAI_API_KEY=$(dotenv_value "${OPENAI_API_KEY:-}")
TOGETHER_API_KEY=$(dotenv_value "${TOGETHER_API_KEY:-}")
MINIMAX_API_KEY=$(dotenv_value "${MINIMAX_API_KEY:-}")

#=== Service Auth (LiteLLM proxy) ===
TARGET_API_KEY=not-needed

#=== LLM Settings (llama-server) ===
MODEL_PROFILE=$(dotenv_value "${MODEL_PROFILE_VALUE}")
# Effective model profile for this hardware: ${MODEL_PROFILE_EFFECTIVE:-qwen}
LLM_MODEL=${LLM_MODEL}
GGUF_FILE=${GGUF_FILE}
GGUF_URL=${GGUF_URL:-}
GGUF_SHA256=${GGUF_SHA256:-}
LLM_MODEL_SIZE_MB=${LLM_MODEL_SIZE_MB:-0}
MAX_CONTEXT=${MAX_CONTEXT}
CTX_SIZE=${MAX_CONTEXT}
MODEL_SELECTION_SOURCE=${MODEL_SELECTION_SOURCE_VALUE}
ODS_ACTIVE_MODEL_STORE=${ODS_ACTIVE_MODEL_STORE:-default}
MODEL_RECOMMENDED_MODEL=${MODEL_RECOMMENDED_MODEL_VALUE}
MODEL_RECOMMENDED_GGUF=${MODEL_RECOMMENDED_GGUF_VALUE}
MODEL_RECOMMENDED_CONTEXT=${MODEL_RECOMMENDED_CONTEXT_VALUE}
MODEL_RECOMMENDATION_SOURCE=$(dotenv_quote "${MODEL_RECOMMENDATION_SOURCE:-installer_tier_map}")
MODEL_RECOMMENDATION_POLICY=$(dotenv_quote "${MODEL_RECOMMENDATION_POLICY:-tier-map}")
MODEL_RECOMMENDATION_CONFIDENCE=$(dotenv_quote "${MODEL_RECOMMENDATION_CONFIDENCE:-medium}")
MODEL_RECOMMENDATION_REASON=$(dotenv_quote "${MODEL_RECOMMENDATION_REASON:-Selected by installer tier ${TIER} (${TIER_NAME}) for ${GPU_BACKEND} backend; benchmark locally after first launch.}")
MODEL_RECOMMENDED_ALTERNATIVES=$(dotenv_quote "${MODEL_RECOMMENDED_ALTERNATIVES:-}")
MODEL_PERFORMANCE_SOURCE=benchmark_required
MODEL_PERFORMANCE_LABEL=$(dotenv_quote "Benchmark after first launch")
MODEL_RUNTIME_PROFILE=$(dotenv_quote "${MODEL_RUNTIME_PROFILE:-}")
MODEL_RUNTIME_PROFILE_LABEL=$(dotenv_quote "${MODEL_RUNTIME_PROFILE_LABEL:-}")
MODEL_RUNTIME_PROFILE_SOURCE=$(dotenv_quote "${MODEL_RUNTIME_PROFILE_SOURCE:-}")
GPU_BACKEND=${GPU_BACKEND}
SYSTEM_RAM_GB=${RAM_GB:-0}
N_GPU_LAYERS=$(dotenv_value "${N_GPU_LAYERS_VALUE}")
$(if [[ -n "${LLAMA_SERVER_IMAGE:-}" ]]; then echo "LLAMA_SERVER_IMAGE=${LLAMA_SERVER_IMAGE}"; fi)
$(if [[ -n "${LLAMA_SERVER_IMAGE_FALLBACK:-}" ]]; then echo "LLAMA_SERVER_IMAGE_FALLBACK=${LLAMA_SERVER_IMAGE_FALLBACK}"; fi)
$(if [[ -n "$LLAMA_SERVER_MEMORY_LIMIT_VALUE" ]]; then echo "LLAMA_SERVER_MEMORY_LIMIT=${LLAMA_SERVER_MEMORY_LIMIT_VALUE}"; fi)
#=== llama.cpp Runtime Tuning ===
LLAMA_ARG_FLASH_ATTN=${LLAMA_ARG_FLASH_ATTN:-auto}
LLAMA_ARG_CACHE_TYPE_K=${LLAMA_ARG_CACHE_TYPE_K:-f16}
LLAMA_ARG_CACHE_TYPE_V=${LLAMA_ARG_CACHE_TYPE_V:-f16}
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
$(if [[ -n "${LLAMA_ARG_N_CPU_MOE:-}" ]]; then echo "LLAMA_ARG_N_CPU_MOE=${LLAMA_ARG_N_CPU_MOE}"; fi)
$(if [[ -n "${LLAMA_ARG_NO_CACHE_PROMPT:-}" ]]; then echo "LLAMA_ARG_NO_CACHE_PROMPT=${LLAMA_ARG_NO_CACHE_PROMPT}"; fi)
$(if [[ -n "${LLAMA_ARG_CHECKPOINT_EVERY_NT:-}" ]]; then echo "LLAMA_ARG_CHECKPOINT_EVERY_NT=${LLAMA_ARG_CHECKPOINT_EVERY_NT}"; fi)
$(if [[ -n "${LLAMA_ARG_CTX_CHECKPOINTS:-}" ]]; then echo "LLAMA_ARG_CTX_CHECKPOINTS=${LLAMA_ARG_CTX_CHECKPOINTS}"; fi)
$(if [[ -n "${LLAMA_ARG_CACHE_RAM:-}" ]]; then echo "LLAMA_ARG_CACHE_RAM=${LLAMA_ARG_CACHE_RAM}"; fi)
$(if [[ -n "${LLAMA_THREADS_VALUE:-}" ]]; then echo "LLAMA_THREADS=${LLAMA_THREADS_VALUE}"; fi)
LLAMA_PARALLEL=${LLAMA_PARALLEL:-1}
# NVIDIA/CPU llama.cpp images default to lossless n-gram speculation (ngram-mod).
# LLAMA_SPEC_TYPE=none turns it off; unset keeps the default.
$(if [[ -n "$LLAMA_SPEC_TYPE_VALUE" ]]; then echo "LLAMA_SPEC_TYPE=$(dotenv_value "$LLAMA_SPEC_TYPE_VALUE")"; fi)
# Optional per-model MTP speculative decoding. Requires an MTP-capable GGUF and llama.cpp build.
# LLAMA_ARG_SPEC_TYPE=draft-mtp
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3
$(if [[ -n "${LLAMA_ARG_SPEC_TYPE:-}" ]]; then echo "LLAMA_ARG_SPEC_TYPE=${LLAMA_ARG_SPEC_TYPE}"; fi)
$(if [[ -n "${LLAMA_ARG_SPEC_DRAFT_N_MAX:-}" ]]; then echo "LLAMA_ARG_SPEC_DRAFT_N_MAX=${LLAMA_ARG_SPEC_DRAFT_N_MAX}"; fi)
$(if [[ -n "${LLAMA_ARG_SPEC_DRAFT_TYPE_K:-}" ]]; then echo "LLAMA_ARG_SPEC_DRAFT_TYPE_K=${LLAMA_ARG_SPEC_DRAFT_TYPE_K}"; fi)
$(if [[ -n "${LLAMA_ARG_SPEC_DRAFT_TYPE_V:-}" ]]; then echo "LLAMA_ARG_SPEC_DRAFT_TYPE_V=${LLAMA_ARG_SPEC_DRAFT_TYPE_V}"; fi)
LLAMA_CPU_LIMIT=${LLAMA_CPU_LIMIT}
LLAMA_CPU_RESERVATION=${LLAMA_CPU_RESERVATION}

# Bundled service CPU budgets. These are capped to CPUs exposed by Docker so
# small hosts do not fail container creation on fixed compose limits.
TTS_WORKERS=$(dotenv_value "${TTS_WORKERS_VALUE}")
TTS_CPU_LIMIT=${TTS_CPU_LIMIT}
TTS_CPU_RESERVATION=${TTS_CPU_RESERVATION}
TTS_THREADS=$(dotenv_value "${TTS_THREADS_VALUE}")
WHISPER_CPU_LIMIT=${WHISPER_CPU_LIMIT}
WHISPER_CPU_RESERVATION=${WHISPER_CPU_RESERVATION}
HERMES_CPU_LIMIT=${HERMES_CPU_LIMIT}
HERMES_CPU_RESERVATION=${HERMES_CPU_RESERVATION}
COMFYUI_CPU_LIMIT=${COMFYUI_CPU_LIMIT}
COMFYUI_CPU_RESERVATION=${COMFYUI_CPU_RESERVATION}

#=== Host File Ownership ===
# Docker Compose reads these from .env without colliding with Bash's readonly UID.
ODS_UID=${_phase06_compose_uid}
ODS_GID=${_phase06_compose_gid}

$(if [[ "$GPU_BACKEND" == "amd" ]]; then
    # HSA_OVERRIDE_GFX_VERSION is read only by the ROCm image, and only a GPU
    # the image was not built for needs it (a wrong value fails model load or
    # hangs the GPU). Vulkan never reads it. An owner-set value is kept.
    _amd_hsa_override="$(_env_get HSA_OVERRIDE_GFX_VERSION \
        "$(ods_amd_hsa_override_for_target "$AMD_INFERENCE_BACKEND_VALUE" "${AMD_GFX_TARGET:-}")")"

    cat << AMD_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)
$(if [[ -n "$_amd_hsa_override" ]]; then printf '\n#=== ROCm gfx override (%s) ===\nHSA_OVERRIDE_GFX_VERSION=%s' "${AMD_GFX_TARGET:-unknown target}" "$(dotenv_value "$_amd_hsa_override")"; fi)
$(if [[ -n "$(_env_get ROCBLAS_USE_HIPBLASLT "")" ]]; then printf 'ROCBLAS_USE_HIPBLASLT=%s' "$(dotenv_value "$(_env_get ROCBLAS_USE_HIPBLASLT "")")"; fi)
AMD_ENV
    unset _amd_hsa_override
fi)
$(if [[ "$GPU_BACKEND" == "sycl" ]]; then cat << INTEL_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== Intel Arc / oneAPI SYCL Settings ===
# Set level_zero:0 on hosts with more than one Intel GPU.
ONEAPI_DEVICE_SELECTOR=$(dotenv_value "$(_env_get ONEAPI_DEVICE_SELECTOR level_zero:gpu)")
ZES_ENABLE_SYSMAN=1
INTEL_ENV
fi)

#=== Ports ===
OLLAMA_PORT=$(dotenv_value "${OLLAMA_PORT_VALUE}")
WEBUI_PORT=$(dotenv_value "${WEBUI_PORT_VALUE}")
DASHBOARD_API_PORT=$(dotenv_value "${DASHBOARD_API_PORT_VALUE}")
SEARXNG_PORT=$(dotenv_value "${SEARXNG_PORT_VALUE}")
PERPLEXICA_PORT=$(dotenv_value "${PERPLEXICA_PORT_VALUE}")
WHISPER_PORT=$(dotenv_value "${WHISPER_PORT_VALUE}")
TTS_PORT=$(dotenv_value "${TTS_PORT_VALUE}")
N8N_PORT=$(dotenv_value "${N8N_PORT_VALUE}")
QDRANT_PORT=$(dotenv_value "${QDRANT_PORT_VALUE}")
QDRANT_GRPC_PORT=$(dotenv_value "${QDRANT_GRPC_PORT_VALUE}")
EMBEDDINGS_PORT=$(dotenv_value "${EMBEDDINGS_PORT_VALUE}")
LITELLM_PORT=$(dotenv_value "${LITELLM_PORT_VALUE}")
LANGFUSE_PORT=$(dotenv_value "${LANGFUSE_PORT}")

#=== Hermes Agent ===
# Hermes streams through model-router (switchboard) or talks to llama-server
# directly; llama.cpp serves one model and accepts any model field. A
# host-native llama-server is reached through LiteLLM, which holds its key.
HERMES_LLM_BASE_URL=$(dotenv_value "${HERMES_LLM_BASE_URL_VALUE}")
HERMES_LLM_API_KEY=$(dotenv_value "${HERMES_LLM_API_KEY_VALUE}")
HERMES_LANGUAGE=${HERMES_LANGUAGE:-en}
HERMES_REQUIRE_OWNER_CARD=${HERMES_REQUIRE_OWNER_CARD:-false}
HERMES_PROXY_PORT=$(dotenv_value "${HERMES_PROXY_PORT_VALUE}")
HERMES_PROXY_UPSTREAM=${HERMES_PROXY_UPSTREAM:-ods-hermes:9119}
ODS_AUTH_UPSTREAM=${ODS_AUTH_UPSTREAM:-ods-dashboard-api:3002}

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=$(dotenv_value "${WEBUI_SECRET}")
DASHBOARD_API_KEY=$(dotenv_value "${DASHBOARD_API_KEY}")
ODS_AGENT_KEY=$(dotenv_value "${ODS_AGENT_KEY}")
ODS_AGENT_BIND=$(dotenv_value "${ODS_AGENT_BIND_VALUE}")
ODS_AGENT_HOST=$(dotenv_value "${ODS_AGENT_HOST_VALUE}")
ODS_AGENT_ADDRESS_MODE=$(dotenv_value "${ODS_AGENT_ADDRESS_MODE_VALUE}")
ODS_SESSION_SECRET=$(dotenv_value "${ODS_SESSION_SECRET}")
HERMES_DASHBOARD_SESSION_TOKEN=$(dotenv_value "${HERMES_DASHBOARD_SESSION_TOKEN}")
$(if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" ]]; then cat << PIXEL_ENV

#=== Pixel core agent (bundled ODS component) ===
PIXEL_AGENT_MODE=pixel
PIXEL_SOURCE_URL=$(dotenv_quote "$PIXEL_SOURCE_URL_VALUE")
PIXEL_SOURCE_REF=$(dotenv_value "${PIXEL_SOURCE_REF_VALUE}")
PIXEL_SOURCE_DIR=$(dotenv_quote "$PIXEL_SOURCE_DIR_VALUE")
$(if [[ -n "$PIXEL_WEB_SEARCH_PROVIDER_VALUE" ]]; then printf 'PIXEL_WEB_SEARCH_PROVIDER=%s\n' "$PIXEL_WEB_SEARCH_PROVIDER_VALUE"; fi)
PIXEL_OPENWEBUI_KEY=$(dotenv_value "${PIXEL_OPENWEBUI_KEY_VALUE}")
PIXEL_MODEL_RELAY_KEY=$(dotenv_value "${PIXEL_MODEL_RELAY_KEY_VALUE}")
PIXEL_MODEL_RELAY_PORT=$(dotenv_value "${PIXEL_MODEL_RELAY_PORT_VALUE}")
PIXEL_INGRESS_RUNTIME_DIR=${PIXEL_INGRESS_RUNTIME_DIR_VALUE}
PIXEL_PREVIEW_RUNTIME_DIR=${PIXEL_PREVIEW_RUNTIME_DIR_VALUE}
PIXEL_RUNTIME_BIND_PROPAGATION=${PIXEL_RUNTIME_BIND_PROPAGATION_VALUE}
PIXEL_INGRESS_GID=${PIXEL_INGRESS_GID_VALUE}
PIXEL_GATEWAY_PORT=$(dotenv_value "${PIXEL_GATEWAY_PORT_VALUE}")
PIXEL_PREVIEW_PORT=$(dotenv_value "${PIXEL_PREVIEW_PORT_VALUE}")
PIXEL_ENV
fi)
SHIELD_API_KEY=$(dotenv_value "${SHIELD_API_KEY}")
N8N_USER=admin@ods.local
N8N_PASS=$(dotenv_value "${N8N_PASS}")
LITELLM_KEY=$(dotenv_value "${LITELLM_KEY}")
LIVEKIT_API_KEY=$(dotenv_value "${LIVEKIT_API_KEY}")
LIVEKIT_API_SECRET=$(dotenv_value "${LIVEKIT_SECRET}")
QDRANT_API_KEY=$(dotenv_value "${QDRANT_API_KEY}")
TOKEN_SPY_API_KEY=$(dotenv_value "${TOKEN_SPY_API_KEY}")
OPENCODE_SERVER_PASSWORD=$(dotenv_value "${OPENCODE_SERVER_PASSWORD}")
SEARXNG_SECRET=$(dotenv_value "${SEARXNG_SECRET}")
DIFY_SECRET_KEY=$(dotenv_value "${DIFY_SECRET_KEY}")

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper acceleration is independently capability-gated from the LLM GPU.
WHISPER_ACCELERATION=$(dotenv_value "${WHISPER_ACCELERATION_VALUE}")
WHISPER_IMAGE=$(dotenv_value "${WHISPER_IMAGE_VALUE}")
# Whisper STT model passed to Open WebUI and pre-downloaded by Phase 12.
AUDIO_STT_MODEL=$(dotenv_value "${AUDIO_STT_MODEL}")
TTS_VOICE=en_US-lessac-medium

#=== Embeddings / RAG ===
# Open WebUI uses this canonical model at every start unless an explicit
# external-provider override is configured.
EMBEDDING_MODEL=$(dotenv_value "${EMBEDDING_MODEL_VALUE}")
RAG_EMBEDDING_MODEL=$(dotenv_value "${RAG_EMBEDDING_MODEL_VALUE}")
RAG_OPENAI_API_BASE_URL=$(dotenv_value "${RAG_OPENAI_API_BASE_URL_VALUE}")
RAG_OPENAI_API_KEY=$(dotenv_value "${RAG_OPENAI_API_KEY_VALUE}")
EMBEDDINGS_MEMORY_LIMIT=$(dotenv_value "${EMBEDDINGS_MEMORY_LIMIT_VALUE}")

#=== Device Name / mDNS / Proxy hostnames ===
# Used by ods-mdns to publish <name>.local on the LAN, by ods-proxy
# to route auth/chat/dashboard subdomains, and by dashboard-api when
# generating magic-link invite URLs. Auto-derived from the system
# hostname at install time so multiple ODS installs on the
# same LAN don't collide on a shared default name. Override by editing
# this line and restarting ods-mdns + ods-proxy.
ODS_DEVICE_NAME=$(dotenv_value "${ODS_DEVICE_NAME}")

#=== Web UI Settings ===
# Loopback installs open directly. Network-bound installs require a login.
WEBUI_AUTH=$(dotenv_value "${WEBUI_AUTH}")
ENABLE_WEB_SEARCH=${ENABLE_WEB_SEARCH:-true}
WEB_SEARCH_ENGINE=searxng

#=== n8n Settings ===
N8N_HOST=localhost
N8N_WEBHOOK_URL=$(dotenv_value "${N8N_WEBHOOK_URL_VALUE}")
TIMEZONE=${SYSTEM_TZ:-UTC}

#=== Langfuse (LLM Observability) ===
LANGFUSE_ENABLED=$(dotenv_value "${LANGFUSE_ENABLED}")
LANGFUSE_NEXTAUTH_SECRET=$(dotenv_value "${LANGFUSE_NEXTAUTH_SECRET}")
LANGFUSE_SALT=$(dotenv_value "${LANGFUSE_SALT}")
LANGFUSE_ENCRYPTION_KEY=$(dotenv_value "${LANGFUSE_ENCRYPTION_KEY}")
LANGFUSE_DB_PASSWORD=$(dotenv_value "${LANGFUSE_DB_PASSWORD}")
LANGFUSE_CLICKHOUSE_PASSWORD=$(dotenv_value "${LANGFUSE_CLICKHOUSE_PASSWORD}")
LANGFUSE_REDIS_PASSWORD=$(dotenv_value "${LANGFUSE_REDIS_PASSWORD}")
LANGFUSE_MINIO_ACCESS_KEY=$(dotenv_value "${LANGFUSE_MINIO_ACCESS_KEY}")
LANGFUSE_MINIO_SECRET_KEY=$(dotenv_value "${LANGFUSE_MINIO_SECRET_KEY}")
LANGFUSE_PROJECT_PUBLIC_KEY=$(dotenv_value "${LANGFUSE_PROJECT_PUBLIC_KEY}")
LANGFUSE_PROJECT_SECRET_KEY=$(dotenv_value "${LANGFUSE_PROJECT_SECRET_KEY}")
LANGFUSE_INIT_PROJECT_ID=$(dotenv_value "${LANGFUSE_INIT_PROJECT_ID}")
LANGFUSE_INIT_USER_EMAIL=$(dotenv_value "${LANGFUSE_INIT_USER_EMAIL}")
LANGFUSE_INIT_USER_PASSWORD=$(dotenv_value "${LANGFUSE_INIT_USER_PASSWORD}")

# ── Image Generation ──
ENABLE_IMAGE_GENERATION=${ENABLE_COMFYUI:-true}

#=== Multi-GPU Settings ===
GPU_COUNT=${GPU_COUNT:-1}
GPU_ASSIGNMENT_JSON_B64=${GPU_ASSIGNMENT_JSON_B64:-}
COMFYUI_GPU_UUID=${COMFYUI_GPU_UUID:-}
WHISPER_GPU_UUID=${WHISPER_GPU_UUID:-}
EMBEDDINGS_GPU_UUID=${EMBEDDINGS_GPU_UUID:-}
LLAMA_SERVER_GPU_UUIDS=${LLAMA_SERVER_GPU_UUIDS:-}
LLAMA_ARG_SPLIT_MODE=${LLAMA_ARG_SPLIT_MODE:-none}
LLAMA_ARG_TENSOR_SPLIT=${LLAMA_ARG_TENSOR_SPLIT:-}
$(if [[ "$GPU_BACKEND" == "amd" && "${GPU_COUNT:-1}" -gt 1 ]]; then cat << AMD_MULTI_ENV

#=== AMD Multi-GPU Settings ===
LLAMA_SERVER_GPU_INDICES=${LLAMA_SERVER_GPU_INDICES:-}
COMFYUI_GPU_INDEX=${COMFYUI_GPU_INDEX:-0}
WHISPER_GPU_INDEX=${WHISPER_GPU_INDEX:-0}
EMBEDDINGS_GPU_INDEX=${EMBEDDINGS_GPU_INDEX:-0}
AMD_MULTI_ENV
fi)

ENV_EOF
    )

    chmod 600 "$INSTALL_DIR/.env"  # Secure secrets file
    if [[ -n "$_phase06_previous_env" ]]; then
        # shellcheck source=../lib/extension-env-carry.sh
        . "$SCRIPT_DIR/installers/lib/extension-env-carry.sh"
        ods_carry_extension_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env" \
            "$SCRIPT_DIR/extensions/services" "$INSTALL_DIR/data/user-extensions"
        ods_carry_public_url_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env"
        ods_carry_named_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env" "${ODS_OWNER_ENV_KEYS[@]}"
        rm -f "$_phase06_previous_env"
    fi
    unset _phase06_previous_env
    # Docker Desktop's daemon is outside the installing WSL namespace.
    # Prepare its authenticated control address before phase 07 starts the
    # host agent and before Compose inherits dashboard-api's environment.
    # shellcheck source=../../lib/wsl-agent-address.sh
    . "$INSTALL_DIR/lib/wsl-agent-address.sh"
    ods_prepare_wsl_agent_address "$INSTALL_DIR" || exit 1
    ai_ok "Created $INSTALL_DIR"
    ai_ok "Generated secure secrets in .env (permissions: 600)"

    # Apply rootless namespace ownership only after the final .env exists.
    # This preserves legacy Token Spy key migration and makes UID/GID overrides
    # from both fresh installs and reruns available to the ownership contract.
    if $_phase06_rootless; then
        export ODS_ROOTLESS_COMPOSE_FLAGS="${COMPOSE_FLAGS:-}"
        if ! ods_fix_rootless_ownership "$INSTALL_DIR"; then
            error "Docker rootless data ownership could not be prepared. Stop any affected ODS services, then re-run the installer."
            return 1
        fi
        unset ODS_ROOTLESS_COMPOSE_FLAGS
    fi

    # Generate the LiteLLM route for an external or host-native model server.
    # The managed container (NVIDIA, AMD, CPU) uses the checked-in local.yaml
    # or the switchboard map rendered below.
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then
        # Fail installation if the external route cannot be materialized. Pixel
        # must never bind its authenticated gateway to a stale local template.
        _external_render_auth=()
        [[ -n "${EXTERNAL_LLM_API_KEY_FILE:-}" ]] && _external_render_auth+=(--external-llm-authenticated)
        if ! "${ODS_PYTHON_CMD:-python3}" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface litellm-external --model "$EXTERNAL_SELECTED_MODEL" \
            --llm-base-url "$EXTERNAL_LLM_CONTAINER_URL_VALUE" \
            "${_external_render_auth[@]}" \
            --output-root "$INSTALL_DIR" --write >> "$LOG_FILE" 2>&1; then
            error "Runtime config renderer failed for the external model gateway"
            return 1
        fi
        unset _external_render_auth
    elif [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then
        _phase06_step "render-native-litellm-config"
        mkdir -p "$INSTALL_DIR/config/litellm"
        # LiteLLM serves ods/current from the native llama-server and sends
        # LLAMA_SERVER_API_KEY (compose passes it to the LiteLLM container);
        # the rendered config names the variable, never the key.
        if ! "${ODS_PYTHON_CMD:-python3}" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface litellm-local-native \
            --gguf-file "$GGUF_FILE" \
            --llm-base-url "${NATIVE_LLM_CONTAINER_BASE_URL_VALUE}/v1" \
            --llm-api-key-env LLAMA_SERVER_API_KEY \
            --output-root "$INSTALL_DIR" \
            --write >> "$LOG_FILE" 2>&1; then
            error "Runtime config renderer failed for the host-native llama-server"
            return 1
        fi
        ai_ok "Generated LiteLLM config for the host-native llama-server (model: ${GGUF_FILE})"
    elif [[ "${EXTERNAL_LLM_RESET:-false}" == "true" && "$ODS_MODE_VALUE" == "local" ]]; then
        # A same-directory rerun does not copy the checked-in local map over
        # the old external map. Reset the actual gateway route, not just .env.
        if ! "${ODS_PYTHON_CMD:-python3}" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface litellm-local --output-root "$INSTALL_DIR" --write \
            >> "$LOG_FILE" 2>&1; then
            error "Runtime config renderer failed while restoring local inference"
            return 1
        fi
    fi

    # Materialize router inputs before Compose can interpret file bind mounts.
    # These files are required even in observe mode so a fresh install never
    # turns a missing file path into a Docker-created directory.
    _phase06_step "render-model-router-config"
    mkdir -p "$INSTALL_DIR/config/model-router" "$INSTALL_DIR/config/litellm"
    _router_renderer_py="${ODS_PYTHON_CMD:-python3}"
    if [[ ! -f "$SCRIPT_DIR/scripts/render-runtime-configs.py" ]] \
        || ! command -v "$_router_renderer_py" >/dev/null 2>&1; then
        error "Model router config renderer is unavailable"
        return 1
    fi
    _router_ods_mode="${ODS_MODE_VALUE:-${ODS_MODE:-local}}"
    # model-router's llama-server-default endpoint is the server origin: the
    # in-stack llama-server, or the host-native server through host-gateway.
    _router_llm_base_url="${LLM_API_URL:-http://llama-server:8080/v1}"
    if [[ "$NATIVE_LLM_ACTIVE" == "true" ]]; then
        _router_llm_base_url="$NATIVE_LLM_CONTAINER_BASE_URL_VALUE"
    fi
    _router_common_args=(
        --switchboard-mode "${ODS_MODEL_SWITCHBOARD_VALUE:-enabled}"
        --ods-mode "$_router_ods_mode"
        --gpu-backend "${GPU_BACKEND:-nvidia}"
        --gguf-file "${GGUF_FILE:-}"
        --llm-base-url "$_router_llm_base_url"
        --output-root "$INSTALL_DIR"
        --write
    )
    # The host-native llama-server requires its key: model-router's endpoint
    # and the switchboard map name LLAMA_SERVER_API_KEY, as the host agent's
    # activation render does.
    [[ "$NATIVE_LLM_ACTIVE" != "true" ]] || _router_common_args+=(--llm-api-key-env LLAMA_SERVER_API_KEY)
    _router_surfaces=(model-router-endpoints)
    if [[ "$_router_ods_mode" != "cloud" ]]; then
        _router_surfaces+=(litellm-switchboard)
    fi
    for _router_surface in "${_router_surfaces[@]}"; do
        if ! ODS_RENDER_LITELLM_KEY="${LITELLM_KEY:-}" \
            "$_router_renderer_py" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface "$_router_surface" "${_router_common_args[@]}" \
            >> "$LOG_FILE" 2>&1; then
            error "Failed to render required ${_router_surface} config"
            return 1
        fi
    done
    unset _router_renderer_py _router_surface _router_common_args
    unset _router_ods_mode _router_surfaces _router_llm_base_url

    # Validate generated .env against schema (fails fast on missing/unknown keys).
    _phase06_step "validate-env"
    ods_progress 41 "directories" "Validating configuration"
    if [[ -f "$SCRIPT_DIR/scripts/validate-env.sh" && -f "$SCRIPT_DIR/.env.schema.json" ]]; then
        if bash "$SCRIPT_DIR/scripts/validate-env.sh" "$INSTALL_DIR/.env" "$SCRIPT_DIR/.env.schema.json" >> "$LOG_FILE" 2>&1; then
            ai_ok "Validated .env against .env.schema.json"
        else
            error "Generated .env failed schema validation. See $LOG_FILE for details."
        fi
    else
        warn "Skipping .env schema validation (.env.schema.json or scripts/validate-env.sh missing)"
    fi

    # Generate SearXNG config with randomized secret key
    # Fix ownership from previous container runs (SearXNG writes as uid 977)
    _phase06_step "generate-searxng-config"
    mkdir -p "$INSTALL_DIR/config/searxng"
    if [[ -f "$INSTALL_DIR/config/searxng/settings.yml" ]] && ! [[ -w "$INSTALL_DIR/config/searxng/settings.yml" ]]; then
        _phase06_repair_host_path "$INSTALL_DIR/config/searxng/settings.yml" "SearXNG configuration" || return 1
    fi
    _searxng_lang="$(ods_searxng_default_lang)"
    cat > "$INSTALL_DIR/config/searxng/settings.yml" << SEARXNG_EOF
use_default_settings: true
server:
  secret_key: "${SEARXNG_SECRET}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
search:
  safe_search: 0
  # Install locale. API clients send no language, so "auto" would mean "all".
  default_lang: "${_searxng_lang}"
  formats:
    - html
    - json
$(ods_searxng_hostnames_yaml "$_searxng_lang")
engines:
  - name: bing
    # Fallback when other general engines are blocked (CAPTCHA/429/access denied).
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: google
    disabled: false
  - name: brave
    disabled: false
  - name: seznam
    # Independent general-web fallback when major engines block this household IP.
    disabled: false
  - name: wikipedia
    disabled: false
  - name: github
    disabled: false
  - name: stackoverflow
    disabled: false
SEARXNG_EOF
    ai_ok "Generated SearXNG config with randomized secret key (search language ${_searxng_lang})"
    unset _searxng_lang
fi

# Documentation, CLI tools, and compose variants already copied by rsync/cp block above
