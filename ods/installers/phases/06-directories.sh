#!/bin/bash
# ============================================================================
# ODS Installer — Phase 06: Directories & Configuration
# ============================================================================
# Part of: installers/phases/
# Purpose: Create directories, copy source files, generate .env, configure
#          OpenClaw, SearXNG, and validate .env schema
#
# Expects: SCRIPT_DIR, INSTALL_DIR, LOG_FILE, DRY_RUN, INTERACTIVE,
#           TIER, TIER_NAME, VERSION, GPU_BACKEND, SYSTEM_TZ,
#           LLM_MODEL, MAX_CONTEXT, GGUF_FILE, COMPOSE_FLAGS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_HERMES, ENABLE_OPENCLAW,
#           OPENCLAW_CONFIG, OPENCLAW_PROVIDER_NAME_DEFAULT,
#           OPENCLAW_PROVIDER_URL_DEFAULT, GPU_ASSIGNMENT_JSON,
#           COMFYUI_GPU_UUID, WHISPER_GPU_UUID, EMBEDDINGS_GPU_UUID,
#           LLAMA_SERVER_GPU_UUIDS, LLAMA_ARG_SPLIT_MODE, LLAMA_ARG_TENSOR_SPLIT,
#           chapter(), ai(), ai_ok(), ai_warn(), log(), warn(), error()
# Provides: WEBUI_SECRET, N8N_PASS, LITELLM_KEY, LIVEKIT_SECRET,
#           DASHBOARD_API_KEY, SHIELD_API_KEY, TOKEN_SPY_API_KEY,
#           OPENCODE_SERVER_PASSWORD,
#           OPENCLAW_TOKEN, OPENCLAW_PROVIDER_NAME, OPENCLAW_PROVIDER_URL,
#           OPENCLAW_MODEL, OPENCLAW_CONTEXT, GPU_ASSIGNMENT_JSON_B64 (in .env)
#           PIXEL_SOURCE_URL, PIXEL_SOURCE_REF, PIXEL_SOURCE_DIR when Pixel is enabled
#
# Modder notes:
#   This is the largest phase. Modify .env generation, add new config files,
#   or change directory layout here.
# ============================================================================

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

if $DRY_RUN; then
    log "[DRY RUN] Would create: $INSTALL_DIR/{config,data,models}"
    log "[DRY RUN] Would copy compose files ($COMPOSE_FLAGS) and source tree"
    log "[DRY RUN] Would generate .env with secrets (WEBUI_SECRET, N8N_PASS, LITELLM_KEY, etc.)"
    log "[DRY RUN] Would generate SearXNG config with randomized secret key"
    [[ "$ENABLE_HERMES" == "true" ]] && log "[DRY RUN] Would configure Hermes Agent (LLM endpoint: http://llama-server:8080/v1; data dir: $INSTALL_DIR/data/hermes)"
    [[ "$ENABLE_OPENCLAW" == "true" ]] && log "[DRY RUN] Would configure OpenClaw (model: $LLM_MODEL, config: ${OPENCLAW_CONFIG:-default})"
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

    # shellcheck source=../../lib/dotenv-quote.sh
    source "$SCRIPT_DIR/lib/dotenv-quote.sh"

    # A Pixel-to-Hermes rerun must retire the exact ODS-managed host runtime,
    # not merely remove the Compose edge from the next launch. Do this before
    # copying new source over an existing install so the fail-closed cleanup can
    # still compare root-owned artifacts with the source that installed them.
    _phase06_pixel_marker="$HOME/.config/ods/pixel-managed.json"
    _phase06_pixel_source_transition=0
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" \
        && -n "${PIXEL_SOURCE_REF:-}" \
        && ( -e "$_phase06_pixel_marker" || -L "$_phase06_pixel_marker" ) ]]; then
        _phase06_pixel_owner="$(ods_pixel_install_owner)" || {
            error "Could not identify the ODS owner for a Pixel source transition."
            return 1
        }
        _phase06_pixel_home="$(ods_pixel_owner_home "$_phase06_pixel_owner")" || {
            error "Could not resolve the ODS owner home for a Pixel source transition."
            return 1
        }
        _ods_pixel_source_transition_required \
            "$_phase06_pixel_owner" "$_phase06_pixel_home" "$PIXEL_SOURCE_REF" \
            || _phase06_pixel_source_transition=$?
        case "$_phase06_pixel_source_transition" in
            0)
                if ! declare -F ods_pixel_uninstall_managed >/dev/null 2>&1; then
                    # shellcheck source=../../lib/pixel-uninstall.sh
                    source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
                fi
                _phase06_step "rebind-pixel-source"
                ai "Retiring the verified prior Pixel source before applying the new immutable source..."
                if ! ods_pixel_uninstall_managed "$INSTALL_DIR" "$_phase06_pixel_home"; then
                    error "Could not safely retire the prior ODS-managed Pixel source."
                    return 1
                fi
                ;;
            1) ;;
            *)
                error "The existing ODS-managed Pixel state is unsafe for a source transition."
                return 1
                ;;
        esac
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
    fi
    unset _phase06_pixel_marker _phase06_pixel_source_transition

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

    _env_existing=""
    [[ -f "$INSTALL_DIR/.env" ]] && _env_existing="$INSTALL_DIR/.env"

    # Safe reader: extract a value from existing .env without sourcing it.
    _env_get() {
        local key="$1" default="${2:-}"
        if [[ -n "$_env_existing" ]]; then
            local val
            val=$(grep -m1 "^${key}=" "$_env_existing" 2>/dev/null | cut -d= -f2- || true)
            val="${val%\"}" && val="${val#\"}"
            val="${val%\'}" && val="${val#\'}"
            if [[ -n "$val" ]]; then
                echo "$val"
                return
            fi
        fi
        echo "$default"
    }

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
    mkdir -p "$INSTALL_DIR"/config/{n8n,litellm,openclaw,searxng}

    _phase06_repair_host_path() {
        local target="$1" description="$2"

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
            error "Cannot repair $description without privileged access: $target. Fix its ownership manually, then re-run ODS."
            return 1
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
        unset _hermes_metadata
    fi

    # Fix ownership of data/config dirs that may have been created by containers
    # (e.g. SearXNG runs as uid 977, ComfyUI data owned by root)
    if ! $_phase06_rootless; then
        for _data_dir in "$INSTALL_DIR"/data/*/; do
            [[ "${ENABLE_HERMES:-false}" == "true" && "$_data_dir" == "$INSTALL_DIR/data/hermes/" ]] && continue
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
        if command -v rsync &>/dev/null; then
            rsync -a --no-owner --no-group \
                --exclude='.git' \
                --exclude='data/' \
                --exclude='logs/' \
                --exclude='models/' \
                --exclude='.env' \
                --exclude='node_modules/' \
                --exclude='dist/' \
                --exclude='*.log' \
                --exclude='.current-mode' \
                --exclude='.profiles' \
                --exclude='.target-model' \
                --exclude='.target-quantization' \
                --exclude='.offline-mode' \
                "$SCRIPT_DIR/" "$INSTALL_DIR/"
        else
            # Fallback: cp -r everything, then remove runtime artifacts
            if ! cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/" 2>>"$LOG_FILE"; then
                warn "Source copy incomplete — some files may be missing"
            fi
            if ! cp "$SCRIPT_DIR"/.gitignore "$INSTALL_DIR/" 2>>"$LOG_FILE"; then
                warn "Failed to copy .gitignore"
            fi
            rm -rf "$INSTALL_DIR/.git" 2>>"$LOG_FILE" || true
        fi
        # Ensure scripts are executable
        chmod +x "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/ods-cli 2>>"$LOG_FILE" || warn "Some scripts may not be executable — verify after install"
        ai_ok "Source files installed"
    else
        log "Running in-place (source == install dir), skipping file copy"
    fi

    # A Windows-mounted WSL checkout can surface every source entry as 0777.
    # Product config and extension code must never remain ambiently writable
    # after installation. Do not follow links; downstream trust checks reject
    # any link where a regular file or directory is required.
    for _installed_code_root in \
        "$INSTALL_DIR/bin" \
        "$INSTALL_DIR/scripts" \
        "$INSTALL_DIR/config" \
        "$INSTALL_DIR/extensions"
    do
        [[ -d "$_installed_code_root" && ! -L "$_installed_code_root" ]] \
            || error "Missing or unsafe installed code tree: $_installed_code_root"
        find -P "$_installed_code_root" \( -type d -o -type f \) -exec chmod go-w -- {} + \
            || error "Could not secure installed code tree: $_installed_code_root"
    done
    find -P "$INSTALL_DIR" -maxdepth 1 -type f \
        \( -name '*.sh' -o -name 'ods-cli' \) -exec chmod go-w -- {} + \
        || error "Could not secure installed root executables"
    unset _installed_code_root

    # Windows-mounted WSL checkouts commonly present every copied file as
    # mode 0777 even when Git records a narrower executable bit. Pixel refuses
    # group/other-writable execution controls by design, so normalize only the
    # two reviewed helpers that are copied into its owner-private runtime.
    _pixel_exec_control_dir="$INSTALL_DIR/extensions/services/pixel-agent/host"
    for _pixel_exec_control in cancellable-exec.sh noninteractive-sudo.sh; do
        _pixel_exec_control_path="$_pixel_exec_control_dir/$_pixel_exec_control"
        [[ -f "$_pixel_exec_control_path" && ! -L "$_pixel_exec_control_path" ]] \
            || error "Missing or unsafe Pixel execution-control helper: $_pixel_exec_control_path"
        chmod 0755 -- "$_pixel_exec_control_path" \
            || error "Could not secure Pixel execution-control helper: $_pixel_exec_control_path"
    done
    unset _pixel_exec_control_dir _pixel_exec_control _pixel_exec_control_path

    _phase06_step "reconcile-pixel-compose"
    if ! ods_pixel_reconcile_installed_compose "$SCRIPT_DIR" "$INSTALL_DIR" "${ENABLE_PIXEL_RUNTIME:-false}"; then
        error "Could not reconcile the installed Pixel Compose fragment with the selected default agent"
    fi

    # ODSForge was retired from the shipped stack after Hermes became the
    # default agent surface. Existing installs may still contain the old
    # bundled extension because the source copy above does not prune removed
    # files. Delete only the retired service code so stale compose files cannot
    # be picked up; preserve data/odsforge for users who want to archive it.
    _retired_odsforge_dir="$INSTALL_DIR/extensions/services/odsforge"
    _phase06_step "prune-retired-services"
    if [[ -d "$_retired_odsforge_dir" ]]; then
        rm -rf "$_retired_odsforge_dir"
        log "Removed retired ODSForge service files from extensions/services"
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
        mkdir -p "$INSTALL_DIR/data/extensions-library"
        cp -r "$_ext_lib_src/." "$INSTALL_DIR/data/extensions-library/"
        [[ ! -L "$INSTALL_DIR/data/extensions-library" ]] \
            || error "Installed extension library cannot be a symlink"
        find -P "$INSTALL_DIR/data/extensions-library" \( -type d -o -type f \) \
            -exec chmod go-w -- {} + \
            || error "Could not secure the installed extension library"
        ai_ok "Extensions library copied to data/extensions-library/ (from $_ext_lib_src)"
    else
        ai_warn "Extensions library not found; dashboard Extensions page will return 503 until populated"
    fi

    # Select tier-appropriate OpenClaw config
    _phase06_step "configure-legacy-openclaw"
    if [[ "$ENABLE_OPENCLAW" == "true" && -n "$OPENCLAW_CONFIG" ]]; then
        OPENCLAW_MODEL="$LLM_MODEL"
        OPENCLAW_CONTEXT=$MAX_CONTEXT

        # Tiers 1/2/3 set OPENCLAW_CONFIG="openclaw.json", which is also the
        # destination filename — skip the self-copy in that case so the file
        # the rsync from SCRIPT_DIR placed there is used as-is.
        _oc_src="$INSTALL_DIR/config/openclaw/$OPENCLAW_CONFIG"
        _oc_dst="$INSTALL_DIR/config/openclaw/openclaw.json"
        if [[ -f "$_oc_src" ]]; then
            if [[ ! "$_oc_src" -ef "$_oc_dst" ]]; then
                cp "$_oc_src" "$_oc_dst"
            fi
        elif [[ -f "$SCRIPT_DIR/config/openclaw/$OPENCLAW_CONFIG" ]]; then
            cp "$SCRIPT_DIR/config/openclaw/$OPENCLAW_CONFIG" "$_oc_dst"
        else
            error "Missing OpenClaw config $OPENCLAW_CONFIG and no fallback present in repo. This is a packaging bug; please re-clone or report."
        fi
        unset _oc_src _oc_dst
        # Resolve provider name/URL before any sed replacements that depend on them
        OPENCLAW_PROVIDER_NAME="${OPENCLAW_PROVIDER_NAME_DEFAULT}"
        OPENCLAW_PROVIDER_URL="${OPENCLAW_PROVIDER_URL_DEFAULT}"

        # Replace model and provider placeholders to match what the inference backend actually serves
        # Escape sed special chars in variable values to prevent injection
        _sed_escape() { printf '%s\n' "$1" | sed 's/[&/\|]/\\&/g'; }
        _oc_model_esc=$(_sed_escape "$OPENCLAW_MODEL")
        _oc_prov_esc=$(_sed_escape "$OPENCLAW_PROVIDER_NAME")
        _sed_i "s|__LLM_MODEL__|${_oc_model_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _sed_i "s|Qwen/Qwen2.5-[^\"]*|${_oc_model_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _sed_i "s|local-ollama|${_oc_prov_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        _oc_key_esc=$(_sed_escape "${LITELLM_KEY:-none}")
        _sed_i "s|__LITELLM_KEY__|${_oc_key_esc}|g" "$INSTALL_DIR/config/openclaw/openclaw.json"
        log "Installed OpenClaw config: $OPENCLAW_CONFIG -> openclaw.json (model: $OPENCLAW_MODEL)"
        # Generate OPENCLAW_TOKEN (used by compose env and inject-token.js)
        OPENCLAW_TOKEN=$(_phase06_generate_hex_secret 24)
        # Note: inject-token.js regenerates /home/node/.openclaw/openclaw.json
        # on every container start, so that file stays ephemeral. OpenClaw also
        # writes agent, cron, and canvas state under /home/node/.openclaw; those
        # paths are bind-mounted under data/openclaw/home. workspace/ is
        # persisted separately under config/openclaw/workspace.
        mkdir -p "$INSTALL_DIR/data/openclaw/home"/{agents,canvas,cron}
        # Create workspace directory (must exist before Docker Compose,
        # otherwise Docker auto-creates it as root and the container can't write to it)
        mkdir -p "$INSTALL_DIR/config/openclaw/workspace/memory"
        # Copy workspace personality files (Todd identity, system knowledge, etc.)
        # Exclude .git and .openclaw dirs — those are runtime/dev artifacts
        if [[ -d "$SCRIPT_DIR/config/openclaw/workspace" ]]; then
            if command -v rsync &>/dev/null; then
                rsync -a --no-owner --no-group --exclude='.git' --exclude='.openclaw' --exclude='.gitkeep' \
                    "$SCRIPT_DIR/config/openclaw/workspace/" "$INSTALL_DIR/config/openclaw/workspace/"
            else
                cp -r "$SCRIPT_DIR/config/openclaw/workspace"/* "$INSTALL_DIR/config/openclaw/workspace/" 2>/dev/null || true
                rm -rf "$INSTALL_DIR/config/openclaw/workspace/.git" 2>/dev/null || true
                rm -rf "$INSTALL_DIR/config/openclaw/workspace/.openclaw" 2>/dev/null || true
            fi
            log "Installed OpenClaw workspace files (agent personality)"
        fi
        # OpenClaw container runs as node (uid 1000) — fix ownership
        # Pre-create data/openclaw so chown doesn't fail on a fresh install where
        # the directory hasn't been touched yet.
        mkdir -p "$INSTALL_DIR/data/openclaw"
        if ! $_phase06_rootless; then
            chown -R 1000:1000 "$INSTALL_DIR/data/openclaw" "$INSTALL_DIR/config/openclaw/workspace" || warn "Failed to chown openclaw paths to 1000:1000 (non-fatal); container may need uid fixup"
        fi
    fi

    # Prepare service-specific ownership after compose selection is final.
    _phase06_step "prepare-service-permissions"
    if ! $_phase06_rootless; then
        chown -R 1000:1000 "$INSTALL_DIR/data/token-spy" || warn "Failed to chown data/token-spy to 1000:1000 (non-fatal); container may crash if installer ran as a different uid"
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
            val="${val%\"}" && val="${val#\"}"
            val="${val%\'}" && val="${val#\'}"
            printf '%s\n' "$val"
            return
        fi
        printf '%s\n' "$default"
    }

    _env_get_explicit_first() {
        local key="$1" default="${2:-}" val
        val="${!key-}"
        if [[ -n "$val" ]]; then
            echo "$val"
            return
        fi
        _env_get "$key" "$default"
    }

    _phase06_detect_lemonade_url() {
        command -v curl >/dev/null 2>&1 || return 1
        local candidate
        for candidate in "http://localhost:13305" "http://localhost:8000"; do
            if curl -fsS --max-time 3 "${candidate}/api/v1/models" >/dev/null 2>&1 || \
               curl -fsS --max-time 3 "${candidate}/api/v1/health" >/dev/null 2>&1 || \
               curl -fsS --max-time 3 "${candidate}/health" >/dev/null 2>&1; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done
        return 1
    }

    _phase06_best_model_id_from_json() {
        local json="$1"
        local py="${ODS_PYTHON_CMD:-}"
        if [[ -z "$py" && -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            py="$(ods_detect_python_cmd 2>/dev/null || true)"
        fi
        [[ -n "$py" ]] || py="python3"
        local selector="$SCRIPT_DIR/scripts/select-external-lemonade-model.py"
        if command -v "$py" >/dev/null 2>&1 && [[ -f "$selector" ]]; then
            printf '%s' "$json" | "$py" "$selector" 2>/dev/null && return 0
        fi
        # A first-id fallback can silently route Pixel to an embedding, speech,
        # or tiny catalog entry.  Fail closed and let the explicit override or
        # phase-12 completion probe provide actionable recovery instead.
        return 1
    }

    _phase06_discover_lemonade_model() {
        local api_base="$1"
        command -v curl >/dev/null 2>&1 || return 1
        local models_json model_id
        models_json="$(curl -fsS --max-time 10 "${api_base%/}/models" 2>/dev/null || true)"
        [[ -n "$models_json" ]] || return 1
        model_id="$(_phase06_best_model_id_from_json "$models_json" || true)"
        [[ -n "$model_id" ]] || return 1
        printf '%s\n' "$model_id"
    }

    # Secrets: reuse existing values, generate only if missing
    WEBUI_SECRET=$(_phase06_env_hex_secret WEBUI_SECRET 32)
    N8N_PASS=$(_env_get N8N_PASS "$(openssl rand -base64 16 2>/dev/null || head -c 16 /dev/urandom | base64)")
    LITELLM_KEY=$(_phase06_env_hex_secret LITELLM_KEY 16 "sk-ods-")
    LITELLM_LEMONADE_API_KEY=$(_phase06_env_hex_secret LITELLM_LEMONADE_API_KEY 16 "sk-ods-lemonade-")
    OPENCLAW_TOKEN=$(_phase06_env_hex_secret OPENCLAW_TOKEN 24)
    LEMONADE_EXTERNAL_VALUE="${LEMONADE_EXTERNAL:-false}"
    [[ "${LEMONADE_EXTERNAL_VALUE,,}" == "true" ]] && LEMONADE_EXTERNAL_VALUE="true" || LEMONADE_EXTERNAL_VALUE="false"
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" && -n "${LEMONADE_API_KEY:-}" ]]; then
        LITELLM_LEMONADE_API_KEY="$LEMONADE_API_KEY"
    fi
    LEMONADE_API_BASE_PATH_VALUE="$(_env_get_explicit_first LEMONADE_API_BASE_PATH "/api/v1")"
    [[ "$LEMONADE_API_BASE_PATH_VALUE" == /* ]] || LEMONADE_API_BASE_PATH_VALUE="/$LEMONADE_API_BASE_PATH_VALUE"
    LEMONADE_BASE_URL_VALUE=""
    LEMONADE_CONTAINER_BASE_URL_VALUE=""
    LEMONADE_PORT_VALUE=""
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        LEMONADE_BASE_URL_VALUE="$(_env_get_explicit_first LEMONADE_BASE_URL "")"
        if [[ -z "$LEMONADE_BASE_URL_VALUE" ]]; then
            if LEMONADE_BASE_URL_VALUE="$(_phase06_detect_lemonade_url)"; then
                ai_ok "Detected existing Lemonade server at ${LEMONADE_BASE_URL_VALUE}"
            else
                LEMONADE_BASE_URL_VALUE="http://localhost:13305"
                warn "Could not auto-detect existing Lemonade; using ${LEMONADE_BASE_URL_VALUE}. Pass --lemonade-url if your server uses another port."
            fi
        fi
        LEMONADE_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE%/}"
        for _lemonade_suffix in "/api/v1" "/v1" "/api"; do
            if [[ "$LEMONADE_BASE_URL_VALUE" == *"$_lemonade_suffix" ]]; then
                LEMONADE_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE%"$_lemonade_suffix"}"
            fi
        done
        LEMONADE_PORT_VALUE="${AMD_INFERENCE_PORT:-}"
        if [[ -z "$LEMONADE_PORT_VALUE" ]]; then
            if [[ "$LEMONADE_BASE_URL_VALUE" =~ ^https?://[^/:]+:([0-9]+)(/|$) ]]; then
                LEMONADE_PORT_VALUE="${BASH_REMATCH[1]}"
            else
                LEMONADE_PORT_VALUE="13305"
            fi
        fi
        LEMONADE_CONTAINER_BASE_URL_VALUE="$(_env_get_explicit_first LEMONADE_CONTAINER_BASE_URL "")"
        if [[ -z "$LEMONADE_CONTAINER_BASE_URL_VALUE" ]]; then
            case "$LEMONADE_BASE_URL_VALUE" in
                http://localhost:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/localhost:/http:\/\/host.docker.internal:}" ;;
                http://127.0.0.1:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/127.0.0.1:/http:\/\/host.docker.internal:}" ;;
                http://[::1]:*) LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_BASE_URL_VALUE/http:\/\/[::1]:/http:\/\/host.docker.internal:}" ;;
                *) LEMONADE_CONTAINER_BASE_URL_VALUE="$LEMONADE_BASE_URL_VALUE" ;;
            esac
        fi
        LEMONADE_CONTAINER_BASE_URL_VALUE="${LEMONADE_CONTAINER_BASE_URL_VALUE%/}"
    fi
    LEMONADE_API_BASE_VALUE="${LEMONADE_BASE_URL_VALUE}${LEMONADE_API_BASE_PATH_VALUE}"
    LEMONADE_CONTAINER_API_BASE_VALUE="$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_CONTAINER_BASE_URL_VALUE}${LEMONADE_API_BASE_PATH_VALUE}"; else echo "http://llama-server:8080/api/v1"; fi)"
    LEMONADE_MODEL_VALUE=""
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        LEMONADE_MODEL_VALUE="$(_env_get_explicit_first LEMONADE_MODEL "")"
        if [[ -z "$LEMONADE_MODEL_VALUE" ]]; then
            if LEMONADE_MODEL_VALUE="$(_phase06_discover_lemonade_model "$LEMONADE_API_BASE_VALUE")"; then
                ai_ok "Detected existing Lemonade model: ${LEMONADE_MODEL_VALUE}"
            elif [[ -n "${GGUF_FILE:-}" ]]; then
                LEMONADE_MODEL_VALUE="extra.${GGUF_FILE}"
                warn "Could not auto-detect a Lemonade model from ${LEMONADE_API_BASE_VALUE}/models; using ${LEMONADE_MODEL_VALUE}. Phase 12 will verify the route before declaring install success."
            else
                warn "Could not auto-detect a Lemonade model from ${LEMONADE_API_BASE_VALUE}/models. Set LEMONADE_MODEL to the id returned by that endpoint."
            fi
        fi
        LEMONADE_MODEL="$LEMONADE_MODEL_VALUE"
    fi
    LIVEKIT_SECRET=$(_env_get LIVEKIT_API_SECRET "$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)")
    LIVEKIT_API_KEY=$(_phase06_env_hex_secret LIVEKIT_API_KEY 16)
    DASHBOARD_API_KEY=$(_phase06_env_hex_secret DASHBOARD_API_KEY 32)
    ODS_AGENT_KEY=$(_phase06_env_hex_secret ODS_AGENT_KEY 32)
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
    PIXEL_INGRESS_GID_VALUE=""
    PIXEL_SOURCE_URL_VALUE=""
    PIXEL_SOURCE_REF_VALUE=""
    PIXEL_SOURCE_DIR_VALUE=""
    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" ]]; then
        PIXEL_OPENWEBUI_KEY_VALUE="$(_env_get PIXEL_OPENWEBUI_KEY "")"
        if [[ -z "$PIXEL_OPENWEBUI_KEY_VALUE" ]]; then
            PIXEL_OPENWEBUI_KEY_VALUE="$(ods_pixel_generate_key)" || error "Could not generate Pixel edge key"
        fi
        [[ "$PIXEL_OPENWEBUI_KEY_VALUE" =~ ^[0-9a-f]{64}$ ]] || error "Existing PIXEL_OPENWEBUI_KEY is invalid"

        # Phase 11 creates/resolves ods-pixel immediately before Compose
        # validation, then atomically fills this initially empty numeric GID.
        PIXEL_INGRESS_GID_VALUE="$(_env_get_preserve_empty PIXEL_INGRESS_GID "")"
        [[ -z "$PIXEL_INGRESS_GID_VALUE" || "$PIXEL_INGRESS_GID_VALUE" =~ ^[1-9][0-9]*$ ]] || \
            error "Existing PIXEL_INGRESS_GID is invalid"

        PIXEL_SOURCE_URL_VALUE="$(_env_get_explicit_first PIXEL_SOURCE_URL "https://github.com/Osmantic/Pixel.git")"
        PIXEL_SOURCE_REF_VALUE="$(_env_get_explicit_first PIXEL_SOURCE_REF "70f44c90ac40b8409ebc965becc5b085a053e270")"
        PIXEL_SOURCE_DIR_VALUE="$(_env_get_explicit_first PIXEL_SOURCE_DIR "")"
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
            error "Pixel source is unavailable. Configure authorized Git access or use a documented clean local checkout before retrying."
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
    fi
    LLAMA_SERVER_MEMORY_LIMIT_VALUE=""
    if [[ "$GPU_BACKEND" == "nvidia" && "$EXTERNAL_LLM_ACTIVE" != "true" && "${ODS_MODE:-local}" != "cloud" ]]; then
        _docker_memory_gb="$(ods_docker_memory_gb 2>/dev/null || true)"
        _effective_memory_gb="$(ods_effective_container_memory_gb "${RAM_GB:-0}" "$_docker_memory_gb")"
        _llama_memory_default="$(ods_default_nvidia_llama_memory_limit "$_effective_memory_gb")"
        LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "${LLAMA_SERVER_MEMORY_LIMIT:-$_llama_memory_default}")"
        unset _docker_memory_gb _effective_memory_gb _llama_memory_default
    fi
    ODS_MODE_VALUE="$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo "local"; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "lemonade"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "lemonade"; else echo "${ODS_MODE:-local}"; fi)"
    ODS_MODEL_SWITCHBOARD_VALUE=$(_env_get ODS_MODEL_SWITCHBOARD "${ODS_MODEL_SWITCHBOARD:-enabled}")
    case "$ODS_MODEL_SWITCHBOARD_VALUE" in
        legacy|observe|enabled) ;;
        *) ODS_MODEL_SWITCHBOARD_VALUE="enabled" ;;
    esac
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" && "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" ]]; then
        ai_warn "External LLM reuse uses the authenticated LiteLLM gateway directly; setting ODS_MODEL_SWITCHBOARD=observe."
        ODS_MODEL_SWITCHBOARD_VALUE="observe"
    fi
    _default_llm_api_url="$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "http://litellm:4000"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "http://litellm:4000"; elif [[ "${ODS_MODE:-local}" == "local" ]]; then echo "http://llama-server:8080"; else echo "http://litellm:4000"; fi)"
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
    elif [[ "$GPU_BACKEND" == "amd" || "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
    else
        _default_hermes_base_url="http://llama-server:8080/v1"
        _default_hermes_api_key="sk-ods-hermes-local"
    fi
    if [[ "$ODS_MODEL_SWITCHBOARD_VALUE" == "enabled" ]]; then
        _default_hermes_base_url="http://litellm:4000/v1"
        _default_hermes_api_key="${LITELLM_KEY}"
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
    LLM_API_URL="$LLM_API_URL_VALUE"
    HERMES_LLM_BASE_URL="$HERMES_LLM_BASE_URL_VALUE"
    HERMES_LLM_API_KEY="$HERMES_LLM_API_KEY_VALUE"
    if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" && "$LEMONADE_BASE_URL_VALUE" =~ ^http://(localhost|127\.0\.0\.1|\[::1\])(:|/|$) && "$(uname -s 2>/dev/null || echo unknown)" == "Linux" ]]; then
        warn "Existing Lemonade URL uses loopback ($LEMONADE_BASE_URL_VALUE). Docker containers will use $LEMONADE_CONTAINER_BASE_URL_VALUE; ensure Lemonade is reachable there (for example: lemonade config set host=0.0.0.0 on a trusted host)."
    fi

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

    TTS_CPU_LIMIT=$(_select_service_cpu_limit TTS_CPU_LIMIT "8.0" "$_docker_available_cpus")
    TTS_CPU_RESERVATION=$(_select_service_cpu_reservation TTS_CPU_RESERVATION "2.0" "$TTS_CPU_LIMIT")
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

    # Host LAN IP — only meaningful when BIND_ADDRESS=0.0.0.0. Some services
    # (e.g. openclaw) need to know the host's LAN address so the Control UI
    # accepts cross-origin requests from LAN clients. Detection prefers
    # `hostname -I` (GNU coreutils, Linux) then `ip route get` then ifconfig
    # so WSL2 + odd Linux variants are covered. Empty default keeps the
    # compose ${HOST_LAN_IP:-} fallback safe when binding to loopback.
    HOST_LAN_IP=""
    if [[ "$BIND_ADDRESS" == "0.0.0.0" ]]; then
        if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
            HOST_LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi
        if [[ -z "$HOST_LAN_IP" ]] && command -v ip >/dev/null 2>&1; then
            HOST_LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
        fi
        if [[ -z "$HOST_LAN_IP" ]] && command -v ifconfig >/dev/null 2>&1; then
            HOST_LAN_IP=$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')
        fi
    fi
    # Preserve operator override across re-runs: if .env already has a value,
    # use it instead of the freshly-detected one (matches the _env_get pattern
    # used for every other persistent value in this phase).
    HOST_LAN_IP=$(_env_get HOST_LAN_IP "$HOST_LAN_IP")

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
        _default_whisper_image="ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu"
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

    _phase06_lemonade_uses_host_9000() {
        [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]] && return 0
        [[ "${AMD_INFERENCE_RUNTIME:-}" =~ ^([Ll][Ee][Mm][Oo][Nn][Aa][Dd][Ee])$ ]] && return 0
        [[ "${GPU_BACKEND:-}" == "amd" && "${ODS_MODE:-local}" != "cloud" ]] && return 0
        return 1
    }

    WHISPER_PORT_VALUE="$(_env_get_explicit_first WHISPER_PORT "9000")"
    if _phase06_lemonade_uses_host_9000 && [[ "$WHISPER_PORT_VALUE" == "9000" ]]; then
        # Lemonade's native Linux/Windows router can reserve host port 9000
        # for websocket traffic. Match the Windows policy: keep Whisper's
        # container port at 8000, but bind the host side on 9100 unless the
        # user already selected another non-9000 port.
        WHISPER_PORT_VALUE="9100"
        ai_ok "AMD/Lemonade detected; Whisper reassigned to host port ${WHISPER_PORT_VALUE}"
    fi
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

    # Generate .env file
    # Subshell-scope a tighter umask so the file is created 0600 from the start
    # (closes a brief window on systems where $HOME is world-readable, e.g.
    # Ubuntu defaults). The umask MUST NOT leak to the rest of phase 06 or
    # subsequent phases — later mkdirs create container-bind-mount dirs that
    # need world-traverse (e.g. SearXNG runs as uid 977, OpenClaw as 1000).
    # The chmod 600 below is belt-and-braces.
    (
        umask 077
        cat > "$INSTALL_DIR/.env" << ENV_EOF
# ODS Configuration — ${TIER_NAME} Edition
# Generated by installer v${VERSION} on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Tier: ${TIER} (${TIER_NAME})

#=== ODS Version (used by ods-cli update for version-compat checks) ===
ODS_VERSION=${VERSION:-2.6.0}

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with --lan or set manually)
BIND_ADDRESS=${BIND_ADDRESS}
# Host LAN IP (populated when BIND_ADDRESS=0.0.0.0; empty otherwise).
# Containers like openclaw read this to advertise the host's LAN address.
HOST_LAN_IP=${HOST_LAN_IP}
# Lets the non-root remote-provider services read only lifecycle secrets that
# the host agent writes mode 0640 under this installation owner's data group.
REMOTE_PROVIDER_DATA_GID=$(id -g 2>/dev/null || echo 1000)

#=== LLM Backend Mode ===
ODS_MODE=${ODS_MODE_VALUE}
ODS_MODEL_SWITCHBOARD=${ODS_MODEL_SWITCHBOARD_VALUE}
LLM_API_URL=${LLM_API_URL_VALUE}
OPEN_WEBUI_LLM_BASE_URL=${OPEN_WEBUI_LLM_BASE_URL_VALUE}
OPEN_WEBUI_LLM_API_KEY=${OPEN_WEBUI_LLM_API_KEY_VALUE}
OPEN_WEBUI_TASK_MODEL=${OPEN_WEBUI_TASK_MODEL_VALUE}
LLM_BACKEND=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo "external"; elif [[ "$ODS_MODE_VALUE" == "lemonade" ]]; then echo "lemonade"; else echo "llama-server"; fi)
LLM_API_BASE_PATH=$(if [[ "$ODS_MODE_VALUE" == "lemonade" ]]; then echo "${LEMONADE_API_BASE_PATH_VALUE}"; else echo "/v1"; fi)
EXTERNAL_LLM_URL=${EXTERNAL_LLM_URL_VALUE}
EXTERNAL_LLM_CONTAINER_URL=${EXTERNAL_LLM_CONTAINER_URL_VALUE}
EXTERNAL_LLM_PROVIDER=${EXTERNAL_LLM_PROVIDER_VALUE}
EXTERNAL_LLM_MODEL=${EXTERNAL_SELECTED_MODEL}
SKIP_MODEL_DOWNLOAD=${EXTERNAL_LLM_ACTIVE}
AMD_INFERENCE_RUNTIME=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" || ( "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ) ]]; then echo "lemonade"; else echo ""; fi)
AMD_INFERENCE_BACKEND=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${AMD_INFERENCE_BACKEND:-auto}"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_LINUX_BACKEND:-rocm}"; else echo ""; fi)
AMD_INFERENCE_LOCATION=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "host"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "container"; else echo ""; fi)
AMD_INFERENCE_PORT=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_PORT_VALUE}"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_API_PORT:-8080}"; else echo ""; fi)
AMD_INFERENCE_SUPPORTED_BACKENDS=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${AMD_INFERENCE_SUPPORTED_BACKENDS:-auto}"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "${BACKEND_LEMONADE_LINUX_BACKEND:-rocm}"; else echo ""; fi)
AMD_INFERENCE_RUNTIME_MODE=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "external-lemonade"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "linux-container"; else echo ""; fi)
AMD_INFERENCE_MANAGED=$(if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then echo ""; elif [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "false"; elif [[ "$GPU_BACKEND" == "amd" && "${ODS_MODE:-local}" == "local" ]]; then echo "true"; else echo ""; fi)
LEMONADE_EXTERNAL=${LEMONADE_EXTERNAL_VALUE}
LEMONADE_BASE_URL=${LEMONADE_BASE_URL_VALUE}
LEMONADE_CONTAINER_BASE_URL=${LEMONADE_CONTAINER_BASE_URL_VALUE}
LEMONADE_API_BASE_PATH=${LEMONADE_API_BASE_PATH_VALUE}
LEMONADE_MODEL=$(if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then echo "${LEMONADE_MODEL_VALUE:-}"; else echo "${LEMONADE_MODEL:-}"; fi)

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
TOGETHER_API_KEY=${TOGETHER_API_KEY:-}
MINIMAX_API_KEY=${MINIMAX_API_KEY:-}

#=== Service Auth (LiteLLM proxy) ===
TARGET_API_KEY=not-needed

#=== LLM Settings (llama-server) ===
MODEL_PROFILE=${MODEL_PROFILE_VALUE}
# Effective model profile for this hardware: ${MODEL_PROFILE_EFFECTIVE:-qwen}
LLM_MODEL=${LLM_MODEL}
GGUF_FILE=${GGUF_FILE}
GGUF_URL=${GGUF_URL:-}
GGUF_SHA256=${GGUF_SHA256:-}
LLM_MODEL_SIZE_MB=${LLM_MODEL_SIZE_MB:-0}
MAX_CONTEXT=${MAX_CONTEXT}
CTX_SIZE=${MAX_CONTEXT}
MODEL_SELECTION_SOURCE=${MODEL_SELECTION_SOURCE_VALUE}
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
N_GPU_LAYERS=${N_GPU_LAYERS_VALUE}
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
$(if [[ -n "${LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS:-}" ]]; then echo "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=${LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS}"; fi)
LLAMA_PARALLEL=${LLAMA_PARALLEL:-1}
# Optional MTP speculative decoding only. Requires an MTP-capable GGUF and llama.cpp build.
# LLAMA_ARG_SPEC_TYPE=draft-mtp
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3
$(if [[ -n "${LLAMA_ARG_SPEC_TYPE:-}" ]]; then echo "LLAMA_ARG_SPEC_TYPE=${LLAMA_ARG_SPEC_TYPE}"; fi)
$(if [[ -n "${LLAMA_ARG_SPEC_DRAFT_N_MAX:-}" ]]; then echo "LLAMA_ARG_SPEC_DRAFT_N_MAX=${LLAMA_ARG_SPEC_DRAFT_N_MAX}"; fi)
LLAMA_CPU_LIMIT=${LLAMA_CPU_LIMIT}
LLAMA_CPU_RESERVATION=${LLAMA_CPU_RESERVATION}

# Bundled service CPU budgets. These are capped to CPUs exposed by Docker so
# small hosts do not fail container creation on fixed compose limits.
TTS_CPU_LIMIT=${TTS_CPU_LIMIT}
TTS_CPU_RESERVATION=${TTS_CPU_RESERVATION}
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
    # Read gfx target from topology detection. Falls back to gfx1151 (Strix Halo)
    # if the topology probe failed — preserves prior behavior for the OG target.
    _amd_gfx_detected=$(echo "${GPU_TOPOLOGY_JSON:-{\}}" | jq -r '[.gpus[]?.gfx_version] | unique | .[0] // "gfx1151"' 2>/dev/null || echo "gfx1151")
    [[ -z "$_amd_gfx_detected" || "$_amd_gfx_detected" == "null" || "$_amd_gfx_detected" == "unknown" ]] && _amd_gfx_detected="gfx1151"

    # HSA_OVERRIDE_GFX_VERSION is a Strix Halo (gfx1151) workaround — that target
    # is not in ROCm 7.x's official support list, so we coerce HSA to load
    # gfx1151 kernels by reporting "11.5.1". For natively-supported parts
    # (gfx942 / MI300X, gfx90a / MI250, gfx1100 / RX 7900, etc.) we MUST NOT set
    # this — doing so reports the wrong ISA and triggers
    # HSA_STATUS_ERROR_INVALID_ISA at model-load.
    case "$_amd_gfx_detected" in
        gfx1151) _amd_hsa_override="HSA_OVERRIDE_GFX_VERSION=11.5.1" ;;
        *)       _amd_hsa_override="# HSA_OVERRIDE_GFX_VERSION unset — $_amd_gfx_detected is natively supported" ;;
    esac

    # The custom llama-server binary at /opt/llama-custom is built with Strix
    # Halo-specific patches (MMQ tile size reduced from 64 to 48 for gfx1151's
    # register file). Pointing Lemonade at it from any other architecture either
    # ISA-faults (kernels compiled for gfx1151) or runs a perf-regressed binary.
    # Only opt-in when the host is actually Strix Halo; otherwise leave unset so
    # docker-compose.amd.yml's empty default lets Lemonade use its bundled
    # ROCm-aware binary.
    if [[ "$_amd_gfx_detected" == "gfx1151" ]]; then
        _amd_custom_bin="LEMONADE_LLAMACPP_ROCM_BIN=/opt/llama-custom/llama-server"
    else
        _amd_custom_bin="# LEMONADE_LLAMACPP_ROCM_BIN unset — custom binary is gfx1151-only; Lemonade uses bundled binary on $_amd_gfx_detected"
    fi

    cat << AMD_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== AMD ROCm Settings (gfx target detected from topology) ===
LEMONADE_SERVER_IMAGE=${LEMONADE_SERVER_IMAGE:-${BACKEND_LEMONADE_CONTAINER_IMAGE:-ghcr.io/lemonade-sdk/lemonade-server:v10.2.0}}
${_amd_hsa_override}
HSA_XNACK=1
ROCBLAS_USE_HIPBLASLT=1
AMDGPU_TARGET=${_amd_gfx_detected}
LLAMA_CPP_REF=b8763
${_amd_custom_bin}

#=== LiteLLM → Lemonade outbound key (AMD only) ===
LITELLM_LEMONADE_API_KEY=${LITELLM_LEMONADE_API_KEY}
AMD_ENV
    unset _amd_gfx_detected _amd_hsa_override _amd_custom_bin
fi)
$(if [[ "$GPU_BACKEND" == "sycl" ]]; then cat << INTEL_ENV
#=== GPU Group IDs (for container device access) ===
VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || echo 44)
RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || echo 992)

#=== Intel Arc / oneAPI SYCL Settings ===
ONEAPI_DEVICE_SELECTOR=level_zero:gpu
SYCL_CACHE_PERSISTENT=1
ZES_ENABLE_SYSMAN=1
INTEL_ENV
fi)

#=== Ports ===
OLLAMA_PORT=11434
WEBUI_PORT=3000
SEARXNG_PORT=8888
PERPLEXICA_PORT=3004
WHISPER_PORT=${WHISPER_PORT_VALUE}
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
EMBEDDINGS_PORT=8090
LITELLM_PORT=4000
OPENCLAW_PORT=7860
LANGFUSE_PORT=${LANGFUSE_PORT}

#=== Hermes Agent ===
# On AMD/Lemonade hosts, route Hermes through litellm. Lemonade is strict
# about model names (returns 404 if model field doesn't exactly match the
# loaded gguf) and drops concurrent connections during multi-step agent
# loops (web_search → reason → tool result → reason …) with
# APIConnectionError. litellm wraps with "*" wildcard normalization +
# retry logic, hiding both bumps. On non-AMD installs, talk direct to
# llama-server (native llama.cpp tolerates any model field).
HERMES_LLM_BASE_URL=${HERMES_LLM_BASE_URL_VALUE}
HERMES_LLM_API_KEY=${HERMES_LLM_API_KEY_VALUE}
HERMES_LANGUAGE=${HERMES_LANGUAGE:-en}
HERMES_PROXY_PORT=${HERMES_PROXY_PORT:-9120}
HERMES_PROXY_UPSTREAM=${HERMES_PROXY_UPSTREAM:-ods-hermes:9119}
ODS_AUTH_UPSTREAM=${ODS_AUTH_UPSTREAM:-ods-dashboard-api:3002}

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=${WEBUI_SECRET}
DASHBOARD_API_KEY=${DASHBOARD_API_KEY}
ODS_AGENT_KEY=${ODS_AGENT_KEY}
ODS_SESSION_SECRET=${ODS_SESSION_SECRET}
HERMES_DASHBOARD_SESSION_TOKEN=${HERMES_DASHBOARD_SESSION_TOKEN}
$(if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true" ]]; then cat << PIXEL_ENV

#=== Pixel default agent (separate written license required) ===
PIXEL_AGENT_MODE=pixel
PIXEL_LICENSE_ACCEPTED=true
PIXEL_SOURCE_URL=$(dotenv_quote "$PIXEL_SOURCE_URL_VALUE")
PIXEL_SOURCE_REF=${PIXEL_SOURCE_REF_VALUE}
PIXEL_SOURCE_DIR=$(dotenv_quote "$PIXEL_SOURCE_DIR_VALUE")
PIXEL_OPENWEBUI_KEY=${PIXEL_OPENWEBUI_KEY_VALUE}
PIXEL_INGRESS_RUNTIME_DIR=/run/ods-pixel
PIXEL_PREVIEW_RUNTIME_DIR=/run/ods-pixel-preview
PIXEL_INGRESS_GID=${PIXEL_INGRESS_GID_VALUE}
PIXEL_ENV
fi)
SHIELD_API_KEY=${SHIELD_API_KEY}
N8N_USER=admin@ods.local
N8N_PASS=${N8N_PASS}
LITELLM_KEY=${LITELLM_KEY}
LIVEKIT_API_KEY=${LIVEKIT_API_KEY}
LIVEKIT_API_SECRET=${LIVEKIT_SECRET}
OPENCLAW_TOKEN=${OPENCLAW_TOKEN}
QDRANT_API_KEY=${QDRANT_API_KEY}
TOKEN_SPY_API_KEY=${TOKEN_SPY_API_KEY}
OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD}
SEARXNG_SECRET=${SEARXNG_SECRET}
DIFY_SECRET_KEY=${DIFY_SECRET_KEY}

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper acceleration is independently capability-gated from the LLM GPU.
WHISPER_ACCELERATION=${WHISPER_ACCELERATION_VALUE}
WHISPER_IMAGE=${WHISPER_IMAGE_VALUE}
# Whisper STT model passed to Open WebUI and pre-downloaded by Phase 12.
AUDIO_STT_MODEL=${AUDIO_STT_MODEL}
TTS_VOICE=en_US-lessac-medium

#=== Embeddings / RAG ===
# Open WebUI uses this canonical model at first boot unless an explicit
# external-provider override is configured.
EMBEDDING_MODEL=${EMBEDDING_MODEL_VALUE}
RAG_EMBEDDING_MODEL=${RAG_EMBEDDING_MODEL_VALUE}
RAG_OPENAI_API_BASE_URL=${RAG_OPENAI_API_BASE_URL_VALUE}
RAG_OPENAI_API_KEY=${RAG_OPENAI_API_KEY_VALUE}
EMBEDDINGS_MEMORY_LIMIT=${EMBEDDINGS_MEMORY_LIMIT_VALUE}

#=== Device Name / mDNS / Proxy hostnames ===
# Used by ods-mdns to publish <name>.local on the LAN, by ods-proxy
# to route auth/chat/dashboard subdomains, and by dashboard-api when
# generating magic-link invite URLs. Auto-derived from the system
# hostname at install time so multiple ODS installs on the
# same LAN don't collide on a shared default name. Override by editing
# this line and restarting ods-mdns + ods-proxy.
ODS_DEVICE_NAME=${ODS_DEVICE_NAME}

#=== Web UI Settings ===
# Loopback installs open directly. Network-bound installs require a login.
WEBUI_AUTH=${WEBUI_AUTH}
ENABLE_WEB_SEARCH=${ENABLE_WEB_SEARCH:-true}
WEB_SEARCH_ENGINE=searxng

#=== n8n Settings ===
N8N_HOST=localhost
N8N_WEBHOOK_URL=http://localhost:5678
TIMEZONE=${SYSTEM_TZ:-UTC}

#=== Langfuse (LLM Observability) ===
LANGFUSE_ENABLED=${LANGFUSE_ENABLED}
LANGFUSE_NEXTAUTH_SECRET=${LANGFUSE_NEXTAUTH_SECRET}
LANGFUSE_SALT=${LANGFUSE_SALT}
LANGFUSE_ENCRYPTION_KEY=${LANGFUSE_ENCRYPTION_KEY}
LANGFUSE_DB_PASSWORD=${LANGFUSE_DB_PASSWORD}
LANGFUSE_CLICKHOUSE_PASSWORD=${LANGFUSE_CLICKHOUSE_PASSWORD}
LANGFUSE_REDIS_PASSWORD=${LANGFUSE_REDIS_PASSWORD}
LANGFUSE_MINIO_ACCESS_KEY=${LANGFUSE_MINIO_ACCESS_KEY}
LANGFUSE_MINIO_SECRET_KEY=${LANGFUSE_MINIO_SECRET_KEY}
LANGFUSE_PROJECT_PUBLIC_KEY=${LANGFUSE_PROJECT_PUBLIC_KEY}
LANGFUSE_PROJECT_SECRET_KEY=${LANGFUSE_PROJECT_SECRET_KEY}
LANGFUSE_INIT_PROJECT_ID=${LANGFUSE_INIT_PROJECT_ID}
LANGFUSE_INIT_USER_EMAIL=${LANGFUSE_INIT_USER_EMAIL}
LANGFUSE_INIT_USER_PASSWORD=${LANGFUSE_INIT_USER_PASSWORD}

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

    # Generate LiteLLM config for Lemonade.
    # Lemonade exposes models as "extra.<GGUF_FILENAME>" — the wildcard
    # passthrough (openai/*) does NOT work because it forwards the friendly
    # model name verbatim and lemonade returns 404.  Instead, map all
    # requests to the concrete model ID that lemonade actually serves.
    # bootstrap-upgrade.sh regenerates this config when the model swaps.
    if [[ "$EXTERNAL_LLM_ACTIVE" == "true" ]]; then
        # Fail installation if the external route cannot be materialized. Pixel
        # must never bind its authenticated gateway to a stale local template.
        if ! "${ODS_PYTHON_CMD:-python3}" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface litellm-external --model "$EXTERNAL_SELECTED_MODEL" \
            --llm-base-url "$EXTERNAL_LLM_CONTAINER_URL_VALUE" \
            --output-root "$INSTALL_DIR" --write >> "$LOG_FILE" 2>&1; then
            error "Runtime config renderer failed for the external model gateway"
            return 1
        fi
    elif [[ "$GPU_BACKEND" == "amd" || "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
        _phase06_step "render-amd-litellm-config"
        mkdir -p "$INSTALL_DIR/config/litellm"
        # Source bootstrap-model.sh for BOOTSTRAP_GGUF_FILE and bootstrap_needed().
        # Pure library (zero side effects), all deps available by phase 06.
        # Phase 11 re-sources it harmlessly (idempotent).
        [[ -f "$SCRIPT_DIR/installers/lib/bootstrap-model.sh" ]] && . "$SCRIPT_DIR/installers/lib/bootstrap-model.sh"
        if type bootstrap_needed &>/dev/null && bootstrap_needed; then
            _active_gguf="$BOOTSTRAP_GGUF_FILE"
        else
            _active_gguf="$GGUF_FILE"
        fi
        _lemonade_model_id=""
        if [[ "$LEMONADE_EXTERNAL_VALUE" == "true" ]]; then
            _lemonade_model_id="${LEMONADE_MODEL_VALUE:-}"
        fi
        # Pass chat_template_kwargs.enable_thinking=false to Lemonade so Qwen3
        # thinking-mode is OFF by default for every client. Perplexica and any
        # other consumer that doesn't manually `/no_think` its prompts would
        # otherwise hang for many minutes per request — the model emits a
        # <think>...</think> block that, on a long synthesis prompt, can run
        # past the client timeout before any visible token reaches the UI.
        # This kwarg is a Qwen3-specific switch and is safely ignored by
        # non-Qwen3 chat templates, so it's safe to bake into the default
        # lemonade config regardless of which model the install resolves to.
        # bootstrap-upgrade.sh mirrors this when it regenerates the file
        # after a hot-swap.
        _renderer_py="${ODS_PYTHON_CMD:-}"
        if [[ -z "$_renderer_py" && -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            _renderer_py="$(ods_detect_python_cmd 2>/dev/null || true)"
        fi
        if [[ -z "$_renderer_py" ]]; then
            _renderer_py="python3"
        fi
        if [[ ! -f "$SCRIPT_DIR/scripts/render-runtime-configs.py" ]] \
            || ! command -v "$_renderer_py" >/dev/null 2>&1; then
            error "Runtime config renderer is unavailable for Lemonade"
            return 1
        fi
        if ! ODS_RENDER_LITELLM_KEY="$LITELLM_LEMONADE_API_KEY" \
            "$_renderer_py" "$SCRIPT_DIR/scripts/render-runtime-configs.py" \
            --surface litellm-lemonade \
            --ods-mode lemonade \
            --gpu-backend amd \
            --gguf-file "$_active_gguf" \
            --lemonade-model-id "$_lemonade_model_id" \
            --lemonade-api-base "$LEMONADE_CONTAINER_API_BASE_VALUE" \
            --output-root "$INSTALL_DIR" \
            --write >> "$LOG_FILE" 2>&1; then
            error "Runtime config renderer failed for Lemonade"
            return 1
        fi
        if [[ -n "$_lemonade_model_id" ]]; then
            ai_ok "Generated LiteLLM config for external Lemonade (model: ${_lemonade_model_id})"
        else
            ai_ok "Generated LiteLLM config for Lemonade (model: extra.${_active_gguf})"
        fi
        unset _renderer_py
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
    _router_common_args=(
        --switchboard-mode "${ODS_MODEL_SWITCHBOARD_VALUE:-enabled}"
        --ods-mode "$_router_ods_mode"
        --gpu-backend "${GPU_BACKEND:-nvidia}"
        --gguf-file "${GGUF_FILE:-}"
        --lemonade-model-id "${LEMONADE_MODEL_VALUE:-}"
        --lemonade-api-base "${LEMONADE_CONTAINER_API_BASE_VALUE:-http://llama-server:8080/api/v1}"
        --llm-base-url "${LLM_API_URL:-http://llama-server:8080/v1}"
        --output-root "$INSTALL_DIR"
        --write
    )
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
    unset _router_ods_mode _router_surfaces

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
    cat > "$INSTALL_DIR/config/searxng/settings.yml" << SEARXNG_EOF
use_default_settings: true
server:
  secret_key: "${SEARXNG_SECRET}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
search:
  safe_search: 0
  formats:
    - html
    - json
engines:
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: google
    disabled: false
  - name: brave
    disabled: false
  - name: wikipedia
    disabled: false
  - name: github
    disabled: false
  - name: stackoverflow
    disabled: false
SEARXNG_EOF
    ai_ok "Generated SearXNG config with randomized secret key"
fi

# Documentation, CLI tools, and compose variants already copied by rsync/cp block above
