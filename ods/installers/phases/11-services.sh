#!/bin/bash
# ============================================================================
# ODS Installer — Phase 11: Start Services
# ============================================================================
# Part of: installers/phases/
# Purpose: Download GGUF model, SDXL Lightning model, generate models.ini, launch
#          Docker Compose stack
#
# Expects: DRY_RUN, INSTALL_DIR, LOG_FILE, GPU_BACKEND,
#           GGUF_FILE, GGUF_URL, LLM_MODEL, MAX_CONTEXT,
#           DOCKER_COMPOSE_CMD, COMPOSE_FLAGS, BGRN, RED, AMB, NC,
#           show_phase(), bootline(), signal(), ai(), ai_ok(), ai_bad(),
#           ai_warn(), log(), spin_task()
# Provides: Running Docker Compose stack
#
# Modder notes:
#   Change model download logic or compose launch flags here.
# ============================================================================

_phase11_build_local_images() {
    local -a build_services=("$@")
    local -a failed_build_services=()
    local build_count=0 build_total=${#build_services[@]}
    local svc build_pid build_failed resolved_image

    for svc in "${build_services[@]}"; do
        build_count=$((build_count + 1))
        $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" build --no-cache "$svc" >> "$LOG_FILE" 2>&1 &
        build_pid=$!
        build_failed=false
        spin_task "$build_pid" "[$build_count/$build_total] Building $svc" || build_failed=true

        # Cross-check that a successful build produced a tagged image. An
        # image left by an earlier install never overrides a non-zero build.
        resolved_image=$($DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" config --format json 2>/dev/null \
            | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    svc_name = '$svc'
    svc_config = d.get('services', {}).get(svc_name, {})
    image = svc_config.get('image', '') or ''
    if not image and svc_config.get('build') is not None:
        project = d.get('name') or 'ods'
        image = f'{project}-{svc_name}'
    print(image)
except Exception:
    pass
" 2>/dev/null || echo "")
        if [[ -n "$resolved_image" ]] && ! $DOCKER_CMD image inspect "$resolved_image" &>/dev/null; then
            build_failed=true
        fi

        if $build_failed; then
            printf "\r  ${AMB}⚠${NC} %-60s\n" "$svc build failed or image missing"
            failed_build_services+=("$svc")
        else
            printf "\r  ${BGRN}✓${NC} %-60s\n" "$svc built"
        fi
    done

    if (( ${#failed_build_services[@]} > 0 )); then
        ai_bad "Required local image build(s) failed: ${failed_build_services[*]}"
        ai "Refusing to start an image left by an earlier install. Fix the build error and rerun the installer."
        return 1
    fi
}

ods_progress 75 "services" "Starting services"
show_phase 5 6 "Starting Services" "~2-3 minutes"

if $DRY_RUN; then
    log "[DRY RUN] Would start services: $DOCKER_COMPOSE_CMD $COMPOSE_FLAGS up -d --remove-orphans --no-build --pull never"
else
    cd "$INSTALL_DIR" || exit 1

    _phase11_env_set() {
        local key="$1" value="$2" env_file="$INSTALL_DIR/.env" tmp_file
        [[ -f "$env_file" ]] || return 0
        tmp_file="${env_file}.tmp.$$"
        awk -v k="$key" -v v="$value" '
            BEGIN { found = 0 }
            index($0, k "=") == 1 { print k "=" v; found = 1; next }
            { print }
            END { if (!found) print k "=" v }
        ' "$env_file" > "$tmp_file" && cat "$tmp_file" > "$env_file" && rm -f "$tmp_file"
    }

    _phase11_env_get() {
        local key="$1" default="${2:-}" env_file="$INSTALL_DIR/.env"
        if [[ -f "$env_file" ]]; then
            local value
            value=$(grep -m1 "^${key}=" "$env_file" 2>/dev/null | cut -d= -f2- || true)
            [[ -n "$value" ]] && { echo "$value"; return 0; }
        fi
        echo "$default"
    }

    _phase11_external_lemonade() {
        local external managed mode
        external="${LEMONADE_EXTERNAL:-$(_phase11_env_get LEMONADE_EXTERNAL false)}"
        managed="${AMD_INFERENCE_MANAGED:-$(_phase11_env_get AMD_INFERENCE_MANAGED "")}"
        mode="${ODS_MODE:-$(_phase11_env_get ODS_MODE local)}"
        [[ "${external,,}" == "true" ]] || [[ "${mode,,}" == "lemonade" && "${managed,,}" == "false" ]]
    }

    _phase11_close_inherited_fds_for_daemon() {
        local fd fd_dir fd_name

        for fd_dir in "/proc/${BASHPID:-$$}/fd" "/dev/fd"; do
            [[ -d "$fd_dir" ]] || continue
            for fd in "$fd_dir"/*; do
                fd_name="${fd##*/}"
                [[ "$fd_name" =~ ^[0-9]+$ ]] || continue
                (( fd_name <= 2 || fd_name == 255 )) && continue
                eval "exec ${fd_name}>&-" 2>/dev/null || true
            done
            return 0
        done

        for ((fd_name = 3; fd_name <= 254; fd_name++)); do
            eval "exec ${fd_name}>&-" 2>/dev/null || true
        done
    }

    _phase11_apply_cpu_fallback() {
        local missing="$1"
        show_amd_gpu_device_guidance "$missing"
        apply_cpu_gpu_fallback "Falling back to CPU mode before launching services."

        if [[ "${TIER_FORCED:-false}" != "true" ]]; then
            TIER="$(select_cpu_fallback_tier "${RAM_GB:-0}")"
            log "CPU fallback tier selected: $TIER"
        fi

        load_backend_contract "cpu" || true
        LLM_HEALTHCHECK_URL="${BACKEND_PUBLIC_HEALTH_URL:-http://localhost:8080/health}"
        LLM_PUBLIC_API_PORT="${BACKEND_PUBLIC_API_PORT:-8080}"
        OPENCLAW_PROVIDER_NAME_DEFAULT="${BACKEND_PROVIDER_NAME:-local-llama}"
        OPENCLAW_PROVIDER_URL_DEFAULT="${BACKEND_PROVIDER_URL:-http://llama-server:8080/v1}"
        resolve_tier_config
        GPU_BACKEND="cpu"

        _phase11_env_set GPU_BACKEND "$GPU_BACKEND"
        _phase11_env_set ODS_MODE "local"
        _phase11_env_set LLM_API_URL "http://llama-server:8080"
        _phase11_env_set LLM_MODEL "$LLM_MODEL"
        _phase11_env_set GGUF_FILE "$GGUF_FILE"
        _phase11_env_set MAX_CONTEXT "$MAX_CONTEXT"
        _phase11_env_set CTX_SIZE "$MAX_CONTEXT"
        _phase11_env_set AUDIO_STT_MODEL "Systran/faster-whisper-base"
        _phase11_env_set LLAMA_SERVER_IMAGE "${LLAMA_SERVER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-b8248}"
        ai_ok "Rewrote .env for CPU fallback"
    }

    _phase11_allow_container_host_firewall() {
        local network_name="${1:-ods-network}"
        local port="$2"
        local rule_label="$3"
        local bind_addr="${4:-}"
        local service_label="${5:-$rule_label}"
        local subnet fw_rule
        local -a subnets=()

        [[ "$(uname -s 2>/dev/null || echo unknown)" == "Linux" ]] || return 0
        command -v systemctl >/dev/null 2>&1 || return 0
        command -v sudo >/dev/null 2>&1 || return 0
        [[ "$port" =~ ^[0-9]+$ ]] || {
            ai_warn "Skipping $service_label firewall rule; invalid port: ${port:-unset}"
            return 0
        }

        if [[ -n "$bind_addr" && "$bind_addr" != "0.0.0.0" ]]; then
            if [[ "$rule_label" == "ods-host-agent" ]]; then
                ai_warn "ODS_AGENT_BIND=$bind_addr; skipping automatic host-agent firewall rule."
            else
                ai_warn "$service_label bind address is $bind_addr; skipping automatic firewall rule."
            fi
            return 0
        fi

        while IFS= read -r subnet; do
            [[ -n "$subnet" && "$subnet" != *:* ]] && subnets+=("$subnet")
        done < <($DOCKER_CMD network inspect "$network_name" \
            --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null || true)

        if [[ ${#subnets[@]} -eq 0 ]]; then
            if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
                ai_warn "UFW is active, but I could not detect the $network_name subnet for $service_label access."
                ai_warn "Inspect: docker network inspect $network_name"
            elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
                ai_warn "firewalld is active, but I could not detect the $network_name subnet for $service_label access."
                ai_warn "Inspect: docker network inspect $network_name"
            fi
            return 0
        fi

        for subnet in "${subnets[@]}"; do
            if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
                if sudo ufw status 2>/dev/null | grep -F "${port}/tcp" | grep -F "$subnet" >/dev/null; then
                    ai_ok "UFW already allows $service_label (port $port) from $network_name subnet $subnet"
                elif sudo ufw allow from "$subnet" to any port "$port" proto tcp comment "$rule_label" 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
                    ai_ok "UFW: allowed $service_label (port $port) from $network_name subnet $subnet"
                else
                    ai_warn "UFW: failed to auto-add $service_label rule - run manually:"
                    ai_warn "  sudo ufw allow from $subnet to any port $port proto tcp comment '$rule_label'"
                fi
            elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
                fw_rule="rule family=\"ipv4\" source address=\"$subnet\" port protocol=\"tcp\" port=\"$port\" accept"
                if sudo firewall-cmd --query-rich-rule="$fw_rule" >/dev/null 2>&1; then
                    ai_ok "firewalld already allows $service_label (port $port) from $network_name subnet $subnet"
                elif sudo firewall-cmd --permanent --add-rich-rule="$fw_rule" 2>&1 | tee -a "$LOG_FILE" >/dev/null \
                  && sudo firewall-cmd --reload 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
                    ai_ok "firewalld: allowed $service_label (port $port) from $network_name subnet $subnet"
                else
                    ai_warn "firewalld: failed to auto-add $service_label rule - run manually:"
                    ai_warn "  sudo firewall-cmd --permanent --add-rich-rule='$fw_rule'"
                    ai_warn "  sudo firewall-cmd --reload"
                fi
            fi
        done
    }

    _phase11_allow_host_agent_firewall() {
        _phase11_allow_container_host_firewall \
            "${1:-ods-network}" \
            "${ODS_AGENT_PORT:-7710}" \
            "ods-host-agent" \
            "${ODS_AGENT_BIND:-}" \
            "ods-host-agent"
    }

    _phase11_allow_external_lemonade_firewall() {
        _phase11_external_lemonade || return 0

        local network_name="${1:-ods-network}"
        local port base without_scheme host_port
        port="${AMD_INFERENCE_PORT:-$(_phase11_env_get AMD_INFERENCE_PORT "")}"
        base="${LEMONADE_BASE_URL:-$(_phase11_env_get LEMONADE_BASE_URL "http://localhost:13305")}"
        base="${base%/}"
        if [[ -z "$port" ]]; then
            without_scheme="${base#*://}"
            host_port="${without_scheme%%/*}"
            if [[ "$host_port" == *:* ]]; then
                port="${host_port##*:}"
            else
                port="13305"
            fi
        fi

        _phase11_allow_container_host_firewall \
            "$network_name" \
            "$port" \
            "ods-external-lemonade" \
            "" \
            "external Lemonade"
    }

    if [[ "${GPU_BACKEND:-}" == "amd" ]] && ! amd_gpu_runtime_devices_available; then
        _amd_missing_devices="$(amd_gpu_missing_devices_csv)"
        if [[ "${GPU_BACKEND_FORCED:-false}" == "true" ]]; then
            ai_bad "GPU_BACKEND=amd was explicitly requested, but required AMD device nodes are missing."
            show_amd_gpu_device_guidance "$_amd_missing_devices"
            exit 1
        fi
        _phase11_apply_cpu_fallback "$_amd_missing_devices"
    fi

    # Re-resolve compose flags against the actual install directory.
    # Phase 03 may have disabled services (e.g., ComfyUI on Tier 0) after
    # COMPOSE_FLAGS was first set in Phase 02, making the cached value stale.
    if [[ -x "$INSTALL_DIR/scripts/resolve-compose-stack.sh" ]]; then
        # --gpu-count is load-bearing: the resolver only adds the multigpu-{backend}.yml
        # overlay when count > 1. Without it, the refreshed value (which we cache
        # to .compose-flags below) would persistently drop multi-GPU overlays
        # for the rest of the install AND every subsequent ods-cli invocation.
        _refreshed_flags=$("$INSTALL_DIR/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" --tier "${TIER:-1}" --gpu-backend "${GPU_BACKEND:-nvidia}" \
            --gpu-count "${GPU_COUNT:-1}" --ods-mode "${ODS_MODE:-local}" 2>/dev/null) || true
        if [[ -n "$_refreshed_flags" ]]; then
            COMPOSE_FLAGS="$_refreshed_flags"
            log "Compose flags refreshed from install directory"
        fi
    fi

    # Convert COMPOSE_FLAGS string to array for safe word-splitting
    read -ra COMPOSE_FLAGS_ARR <<< "$COMPOSE_FLAGS"
    mkdir -p "$INSTALL_DIR/logs"

    # Persist compose flags so ods-cli can reuse them without re-resolving
    echo "$COMPOSE_FLAGS" > "$INSTALL_DIR/.compose-flags" || warn "Could not cache compose flags (non-fatal)"
    log "Saved compose flags to $INSTALL_DIR/.compose-flags"

    _phase11_compose_command_text() {
        printf '%s' "$DOCKER_COMPOSE_CMD"
        printf ' %s' "${COMPOSE_FLAGS_ARR[@]}"
    }

    _phase11_compose_up_suffix() {
        printf '%s' 'up -d --remove-orphans --no-build --pull never'
    }

    _phase11_write_compose_launch_record() {
        local path="$INSTALL_DIR/logs/compose-launch.txt"
        local command_text up_suffix
        command_text="$(_phase11_compose_command_text)"
        up_suffix="$(_phase11_compose_up_suffix)"
        {
            printf 'timestamp=%s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
            printf 'cwd=%s\n' "$INSTALL_DIR"
            printf 'compose_command=%s %s\n' "$command_text" "$up_suffix"
            printf 'compose_flags=%s\n' "${COMPOSE_FLAGS_ARR[*]}"
            printf 'compose_flags_file=%s\n' "$INSTALL_DIR/.compose-flags"
            printf "compose_ps_command=cd '%s' && %s ps -a\n" "$INSTALL_DIR" "$command_text"
            printf "compose_logs_command=cd '%s' && %s logs --tail 200\n" "$INSTALL_DIR" "$command_text"
            printf 'compose_files=\n'
            local _expect_file=false _arg
            for _arg in "${COMPOSE_FLAGS_ARR[@]}"; do
                if $_expect_file; then
                    printf '  - %s\n' "$_arg"
                    _expect_file=false
                elif [[ "$_arg" == "-f" ]]; then
                    _expect_file=true
                fi
            done
        } > "$path"
        log "Saved compose launch record to $path"
    }

    _phase11_assert_managed_containers() {
        local write_report="${1:-true}"
        local command_text up_suffix ids count report_path
        command_text="$(_phase11_compose_command_text)"
        up_suffix="$(_phase11_compose_up_suffix)"
        ids="$($DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" ps -q 2>>"$LOG_FILE" || true)"
        count=$(printf '%s\n' "$ids" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')
        if [[ "${count:-0}" -gt 0 ]]; then
            log "Compose managed container count after launch: $count"
            return 0
        fi

        ai_bad "Docker Compose did not create any managed containers."
        ai "Launch record: $INSTALL_DIR/logs/compose-launch.txt"
        ai "Inspect with:"
        ai "  cd '$INSTALL_DIR' && $command_text ps -a"
        ai "  cd '$INSTALL_DIR' && $command_text logs --tail 200"
        if [[ "$write_report" != "false" ]] && command -v write_compose_failure_report >/dev/null 2>&1; then
            report_path="$(COMPOSE_FLAGS_REPORT="${COMPOSE_FLAGS_ARR[*]}" write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-core phase 11 zero managed containers" \
                "$command_text $up_suffix" \
                "$LOG_FILE" \
                "${GPU_BACKEND:-unknown}" \
                "No ODS containers were created. Run the saved ps/logs commands from the launch record, fix the compose/runtime failure, then re-run ./install.sh." |
                tail -n 1)" || true
            [[ -n "${report_path:-}" ]] && ai_warn "Compose failure report saved: $report_path"
        fi
        return 1
    }

    _phase11_has_managed_containers() {
        local ids count
        ids="$($DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" ps -q 2>>"$LOG_FILE" || true)"
        count=$(printf '%s\n' "$ids" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')
        if [[ "${count:-0}" -gt 0 ]]; then
            log "Compose managed container count after delayed-health launch: $count"
            return 0
        fi
        return 1
    }

    _phase11_compose_failure_is_delayed_health() {
        local log_path="${LOG_FILE:-}"
        [[ -n "$log_path" && -f "$log_path" ]] || return 1
        grep -Eiq 'dependency failed to start: container ods-(llama-server|llama-ready|llama-server-ready) is unhealthy' "$log_path"
    }

    _phase11_pre_pull_compose_images() {
        command -v ods_compose_external_images >/dev/null 2>&1 || return 0
        command -v pull_with_progress >/dev/null 2>&1 || return 0

        local -a images=()
        local image image_output count total failed command_text up_suffix report_path
        image_output=""
        if ! image_output="$(ods_compose_external_images "$DOCKER_COMPOSE_CMD" "${COMPOSE_FLAGS_ARR[@]}" 2>>"$LOG_FILE")"; then
            ai_bad "Could not resolve Docker Compose images before service launch"
            ai "Inspect compose config with: $(_phase11_compose_command_text) config --images"
            return 1
        fi
        if [[ -n "$image_output" ]]; then
            mapfile -t images <<< "$image_output"
        fi

        [[ ${#images[@]} -gt 0 ]] || return 0

        ai "Verifying Compose image cache before launch..."
        count=0
        total=${#images[@]}
        failed=0
        for image in "${images[@]}"; do
            count=$((count + 1))
            if $DOCKER_CMD image inspect "$image" >/dev/null 2>&1; then
                log "Compose image already cached: $image"
                continue
            fi
            if ! pull_with_progress "$image" "COMPOSE — ${image}" "$count" "$total"; then
                failed=$((failed + 1))
            fi
        done

        if [[ $failed -eq 0 ]]; then
            ai_ok "Compose image cache ready"
            return 0
        fi

        ai_bad "$failed Compose image(s) could not be pulled before launch"
        ai "Phase 5 does not allow Docker Compose to pull images implicitly."
        ai "Fix the registry/network/disk error above, then re-run ./install.sh."
        if command -v write_compose_failure_report >/dev/null 2>&1; then
            command_text="$(_phase11_compose_command_text)"
            up_suffix="$(_phase11_compose_up_suffix)"
            report_path="$(COMPOSE_FLAGS_REPORT="${COMPOSE_FLAGS_ARR[*]}" write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-core phase 11 compose image preflight" \
                "$command_text $up_suffix" \
                "$LOG_FILE" \
                "${GPU_BACKEND:-unknown}" \
                "A required Compose image did not download during the retry-protected preflight. Fix Docker registry/network/disk access, then re-run ./install.sh." |
                tail -n 1)" || true
            [[ -n "${report_path:-}" ]] && ai_warn "Compose failure report saved: $report_path"
        fi
        return 1
    }

    # Cloud/external Lemonade modes skip ODS-managed GGUF downloads and
    # auto-enable LiteLLM because it is the routing surface for both paths.
    if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
        ai "Cloud mode — skipping model download"
        # Auto-enable litellm extension
        litellm_cf="$INSTALL_DIR/extensions/services/litellm/compose.yaml"
        litellm_disabled="${litellm_cf}.disabled"
        if [[ -f "$litellm_disabled" && ! -f "$litellm_cf" ]]; then
            mv "$litellm_disabled" "$litellm_cf"
            ai_ok "Auto-enabled litellm for cloud mode"
        fi
    elif _phase11_external_lemonade; then
        ai "Existing Lemonade mode - skipping ODS-managed GGUF download"
        litellm_cf="$INSTALL_DIR/extensions/services/litellm/compose.yaml"
        litellm_disabled="${litellm_cf}.disabled"
        if [[ -f "$litellm_disabled" && ! -f "$litellm_cf" ]]; then
            mv "$litellm_disabled" "$litellm_cf"
            ai_ok "Auto-enabled litellm for external Lemonade mode"
        fi
    fi

    # Ensure model directory exists
    mkdir -p "$INSTALL_DIR/data/models"

    # ── Bootstrap model fast-start ──
    # For Tier 1+ installs, download a tiny model first so the user can chat
    # immediately. The full model downloads in the background and hot-swaps.
    [[ -f "$SCRIPT_DIR/installers/lib/bootstrap-model.sh" ]] && . "$SCRIPT_DIR/installers/lib/bootstrap-model.sh"
    _BOOTSTRAP_ACTIVE=false
    if type bootstrap_needed &>/dev/null && bootstrap_needed; then
        _BOOTSTRAP_ACTIVE=true
        # Save full model config for the background upgrade
        FULL_GGUF_FILE="$GGUF_FILE"
        FULL_GGUF_URL="$GGUF_URL"
        FULL_GGUF_SHA256="$GGUF_SHA256"
        FULL_LLM_MODEL="$LLM_MODEL"
        FULL_MAX_CONTEXT="$MAX_CONTEXT"

        # Swap to bootstrap model for the foreground download
        GGUF_FILE="$BOOTSTRAP_GGUF_FILE"
        GGUF_URL="$BOOTSTRAP_GGUF_URL"
        GGUF_SHA256=""  # No SHA256 for Tier 0 model
        LLM_MODEL="$BOOTSTRAP_LLM_MODEL"
        MAX_CONTEXT="$BOOTSTRAP_MAX_CONTEXT"
        ai "Fast-start mode: downloading bootstrap model (~1.5GB) for instant chat."
        ai "Your full model ($FULL_LLM_MODEL) will download in the background."
    fi


    # Download GGUF model if not already present (with retry and integrity verification)
    ods_progress 76 "services" "Checking AI model"
    GGUF_DIR="$INSTALL_DIR/data/models"
    if [[ "${ODS_MODE:-local}" != "cloud" && -n "$GGUF_URL" ]] && ! _phase11_external_lemonade; then
        # Check if model exists and verify integrity
        if [[ -f "$GGUF_DIR/$GGUF_FILE" ]]; then
            if [[ -n "$GGUF_SHA256" ]]; then
                if command -v sha256sum &>/dev/null; then
                    ai "Verifying model integrity (SHA256)..."
                    ACTUAL_HASH=$(sha256sum "$GGUF_DIR/$GGUF_FILE" 2>/dev/null | awk '{print $1}')
                    if [[ -n "$ACTUAL_HASH" && "$ACTUAL_HASH" == "$GGUF_SHA256" ]]; then
                        ai_ok "Model verified: $GGUF_FILE"
                    elif [[ -z "$ACTUAL_HASH" ]]; then
                        ai_warn "Could not compute checksum for existing model file"
                        ai_ok "GGUF model already present: $GGUF_FILE (verification skipped)"
                    else
                        ai_warn "Model file is corrupt (SHA256 mismatch)."
                        ai "  Expected: $GGUF_SHA256"
                        ai "  Got:      $ACTUAL_HASH"
                        ai "Removing corrupt file and re-downloading..."
                        rm -f "$GGUF_DIR/$GGUF_FILE"
                    fi
                else
                    ai_warn "sha256sum not available, skipping integrity check"
                    ai_ok "GGUF model already present: $GGUF_FILE (verification skipped)"
                fi
            else
                ai_ok "GGUF model already present: $GGUF_FILE"
            fi
        fi

        # Download if not present or was removed due to corruption
        if [[ ! -f "$GGUF_DIR/$GGUF_FILE" ]]; then
            ods_progress 77 "services" "Downloading AI model"
            ai "Downloading GGUF model: $GGUF_FILE"
            signal "This is the big one. I've got it — sit back."
            echo ""

            # Retry loop: up to 3 attempts with resume support (-c flag)
            _dl_success=false
            for _attempt in 1 2 3; do
                [[ $_attempt -gt 1 ]] && ai "Retry attempt $_attempt of 3..."
                curl -fSL -C - --connect-timeout 30 --max-time 3600 \
                    --retry 3 --retry-delay 5 --retry-all-errors \
                    -o "$GGUF_DIR/$GGUF_FILE.part" "$GGUF_URL" \
                    >> "$INSTALL_DIR/logs/model-download.log" 2>&1 &
                dl_pid=$!

                if spin_task $dl_pid "Downloading $GGUF_FILE"; then
                    # Verify the file actually landed before claiming success.
                    # Today's chain (spin_task → mv → printf) trusts each step's
                    # exit code separately and can race: mv can silently fail if
                    # the target dir is read-only or .part was truncated, or
                    # another process can remove the file before the printf
                    # fires. A spurious "Model downloaded" line then misleads
                    # later phases that depend on the file existing.
                    if mv "$GGUF_DIR/$GGUF_FILE.part" "$GGUF_DIR/$GGUF_FILE" && [[ -s "$GGUF_DIR/$GGUF_FILE" ]]; then
                        printf "\r  ${BGRN}✓${NC} %-60s\n" "Model downloaded: $GGUF_FILE"
                        _dl_success=true
                        break
                    else
                        rm -f "$GGUF_DIR/$GGUF_FILE" 2>/dev/null || true
                        printf "\r  ${AMB}⚠${NC} %-60s\n" "Download claimed to succeed but $GGUF_FILE is missing/empty"
                    fi
                fi
                printf "\r  ${AMB}⚠${NC} %-60s\n" "Download attempt $_attempt failed"
                sleep 3
            done

            if [[ "$_dl_success" != "true" ]]; then
                printf "\r  ${RED}✗${NC} %-60s\n" "Download failed after 3 attempts: $GGUF_FILE"
                ai "Manual retry: curl -fSL -C - --connect-timeout 30 --max-time 3600 --retry 3 --retry-delay 5 --retry-all-errors -o '$GGUF_DIR/$GGUF_FILE.part' '$GGUF_URL' && mv '$GGUF_DIR/$GGUF_FILE.part' '$GGUF_DIR/$GGUF_FILE'"
            else
                # Verify freshly downloaded file
                if [[ -n "$GGUF_SHA256" ]]; then
                    if command -v sha256sum &>/dev/null; then
                        ai "Verifying download integrity (SHA256)..."
                        ACTUAL_HASH=$(sha256sum "$GGUF_DIR/$GGUF_FILE" 2>/dev/null | awk '{print $1}')
                        if [[ -n "$ACTUAL_HASH" && "$ACTUAL_HASH" == "$GGUF_SHA256" ]]; then
                            ai_ok "Download verified OK"
                        elif [[ -z "$ACTUAL_HASH" ]]; then
                            ai_warn "Could not compute checksum for downloaded file"
                            ai_warn "Proceeding without verification (file may be corrupt)"
                        else
                            printf "\r  ${RED}✗${NC} %-60s\n" "Downloaded file is corrupt (SHA256 mismatch)"
                            ai "  Expected: $GGUF_SHA256"
                            ai "  Got:      $ACTUAL_HASH"
                            rm -f "$GGUF_DIR/$GGUF_FILE"
                            ai_warn "Corrupt file removed. Re-run installer to download again."
                            _dl_success=false
                        fi
                    else
                        ai_warn "sha256sum not available, skipping integrity check"
                        ai_warn "Proceeding without verification (file may be corrupt)"
                    fi
                fi
            fi
        fi

        # Abort if model download/verification failed
        if [[ "${ODS_MODE:-local}" != "cloud" && -n "$GGUF_URL" && ! -f "$GGUF_DIR/$GGUF_FILE" ]] && ! _phase11_external_lemonade; then
            ai_bad "Model file missing or verification failed. Cannot proceed without a valid model."
            ai "Re-run the installer to retry the download."
            exit 1
        fi
    fi

    # ── SDXL Lightning model download (ComfyUI image generation) ──
    ods_progress 79 "services" "Checking image generation models"
    if [[ "$ENABLE_COMFYUI" != "true" ]]; then
        ai "Image generation disabled — skipping model download"
    elif [[ "${ODS_MODE:-local}" == "cloud" ]]; then
        ai "Cloud mode — skipping image model download"
    elif [[ "$GPU_BACKEND" == "amd" ]]; then
        COMFYUI_BASE="$INSTALL_DIR/data/comfyui/ComfyUI/models"
    elif [[ "$GPU_BACKEND" == "nvidia" ]]; then
        COMFYUI_BASE="$INSTALL_DIR/data/comfyui/models"
    fi
    if [[ "$ENABLE_COMFYUI" == "true" && "${ODS_MODE:-local}" != "cloud" && ( "$GPU_BACKEND" == "amd" || "$GPU_BACKEND" == "nvidia" ) ]]; then
        SDXL_CHECKPOINT_DIR="$COMFYUI_BASE/checkpoints"
        mkdir -p "$SDXL_CHECKPOINT_DIR"
        # NVIDIA ComfyUI also needs output/input/workflows bind-mount dirs
        if [[ "$GPU_BACKEND" == "nvidia" ]]; then
            mkdir -p "$INSTALL_DIR/data/comfyui"/{output,input,workflows}
        fi

        SDXL_MODEL="sdxl_lightning_4step.safetensors"
        SDXL_URL="https://huggingface.co/ByteDance/SDXL-Lightning/resolve/main/sdxl_lightning_4step.safetensors"

        if [[ ! -f "$SDXL_CHECKPOINT_DIR/$SDXL_MODEL" ]]; then
            ai "Downloading SDXL Lightning 4-step (~6.5GB) for image generation..."

            # Source background task tracking
            if [[ -f "$SCRIPT_DIR/installers/lib/background-tasks.sh" ]]; then
                . "$SCRIPT_DIR/installers/lib/background-tasks.sh"
            fi

            # This daemon must not inherit the installer model lifecycle lock;
            # otherwise full-model activation waits for an unrelated 6.5 GB
            # image download after the installer itself releases the lock.
            (
                _phase11_close_inherited_fds_for_daemon
                exec nohup env \
                    SDXL_CHECKPOINT_DIR="$SDXL_CHECKPOINT_DIR" \
                    SDXL_MODEL="$SDXL_MODEL" \
                    SDXL_URL="$SDXL_URL" \
                    bash -c '
                        echo "[SDXL] Starting SDXL Lightning model download..."
                        if [[ ! -f "$SDXL_CHECKPOINT_DIR/$SDXL_MODEL" ]]; then
                            echo "[SDXL] Downloading $SDXL_MODEL (~6.5GB)..."
                            curl -fSL -C - --connect-timeout 30 --max-time 3600 \
                                --retry 5 --retry-delay 10 --retry-all-errors \
                                -o "$SDXL_CHECKPOINT_DIR/$SDXL_MODEL.part" \
                                "$SDXL_URL" 2>&1 && \
                                mv "$SDXL_CHECKPOINT_DIR/$SDXL_MODEL.part" "$SDXL_CHECKPOINT_DIR/$SDXL_MODEL" && \
                                echo "[SDXL] $SDXL_MODEL complete" || \
                                echo "[SDXL] ERROR: Failed to download $SDXL_MODEL"
                        fi
                        echo "[SDXL] SDXL Lightning model download finished."
                    ' > "$INSTALL_DIR/logs/sdxl-download.log" 2>&1
            ) &

            sdxl_pid=$!

            # Register background task
            if command -v bg_task_start &>/dev/null; then
                bg_task_start "sdxl-download" "$sdxl_pid" "SDXL Lightning model download" "$INSTALL_DIR/logs/sdxl-download.log"
            fi

            log "Background SDXL download started (PID: $sdxl_pid). Check: tail -f $INSTALL_DIR/logs/sdxl-download.log"
            ai "SDXL Lightning downloading in background (~6.5GB). ComfyUI will be ready once complete."
        else
            ai_ok "SDXL Lightning model already present"
        fi
    fi

    # Generate models.ini for llama-server (skip in cloud mode)
    if [[ "${ODS_MODE:-local}" != "cloud" ]] && ! _phase11_external_lemonade; then
        mkdir -p "$INSTALL_DIR/config/llama-server"
        cat > "$INSTALL_DIR/config/llama-server/models.ini" << MODELS_INI_EOF
[${LLM_MODEL}]
filename = ${GGUF_FILE}
load-on-startup = true
n-ctx = ${MAX_CONTEXT}
MODELS_INI_EOF
        ai_ok "Generated models.ini for llama-server"

        # If bootstrap is active, patch .env so docker compose starts llama-server
        # with the bootstrap model (phase 06 wrote .env with the full model values)
        if [[ "$_BOOTSTRAP_ACTIVE" == "true" ]]; then
            _env_file="$INSTALL_DIR/.env"
            if [[ -f "$_env_file" ]]; then
                _env_patch_ok=true
                for _key_val in "GGUF_FILE=$GGUF_FILE" "LLM_MODEL=$LLM_MODEL" "MAX_CONTEXT=$MAX_CONTEXT" "CTX_SIZE=$MAX_CONTEXT"; do
                    _key="${_key_val%%=*}"
                    _val="${_key_val#*=}"
                    if awk -v v="$_val" '{ if (index($0, "'"$_key"'=") == 1) print "'"$_key"'=" v; else print }' \
                        "$_env_file" > "${_env_file}.tmp" 2>>"$LOG_FILE" \
                        && cat "${_env_file}.tmp" > "$_env_file" 2>>"$LOG_FILE" \
                        && rm -f "${_env_file}.tmp"; then
                        # Verify the patch took effect — the awk-then-cat
                        # chain can succeed bytewise while landing a line
                        # that isn't what we asked for (e.g. when $_val
                        # contains awk-meta characters or the original
                        # line had trailing whitespace the regex didn't
                        # match). Re-read the file and assert.
                        if grep -Fqx "${_key}=${_val}" "$_env_file"; then
                            : # confirmed
                        else
                            _env_patch_ok=false
                            warn "Patched $_key in .env, but verification re-read shows a different value (expected '$_val')"
                        fi
                    else
                        _env_patch_ok=false
                        warn "Failed to patch $_key in .env"
                    fi
                done
                if [[ "$_env_patch_ok" == "true" ]]; then
                    ai_ok "Patched .env for bootstrap model ($GGUF_FILE)"
                fi
            fi

            # End-of-bootstrap-block sanity: refuse to leave Phase 11 with
            # $LLM_MODEL pointing at a file that isn't on disk. Without this
            # guard, compose-up brings up llama-server, which immediately
            # crash-loops trying to open a missing GGUF, and the operator
            # spends the next ~20 minutes watching the linker retry. Better
            # to surface the missing file here, while there's still a clean
            # recovery path (re-run the download, fix .env, then resume).
            if [[ -n "${GGUF_DIR:-}" && -n "${GGUF_FILE:-}" ]]; then
                if [[ ! -s "$GGUF_DIR/$GGUF_FILE" ]]; then
                    warn "Bootstrap sanity: $GGUF_DIR/$GGUF_FILE missing or empty after Phase 11 — llama-server will crash-loop on compose-up. Investigate before proceeding."
                fi
            fi
        fi
    fi

    if [[ "${ENABLE_HERMES:-false}" == "true" ]]; then
        # The Hermes Agent extension ships a config template at
        # extensions/services/hermes/cli-config.yaml.template which is
        # mounted into the container at /opt/hermes/cli-config.yaml.example.
        # On first container start, Hermes's entrypoint copies that into
        # /opt/data/config.yaml — and never reads it again. So the values
        # we want Hermes to use (model name, base_url) need to land in the
        # template BEFORE compose-up, not after.
        #
        # Two values vary per platform / backend and the template ships
        # placeholders for both:
        #   model.default — Hermes asks the LLM server for this exact name.
        #                   llama.cpp serves under "<file>.gguf"; Lemonade
        #                   (AMD) wraps it as "extra.<file>.gguf". Asking
        #                   for the wrong name 404s every chat completion.
        #   model.base_url — llama-server's URL. The compose bridge name
        #                   "llama-server:8080" works for the Linux installs,
        #                   but on macOS llama-server runs native on the
        #                   host (not as a sibling container), so Hermes
        #                   has to dial "host.docker.internal:8080".
        #
        # Substitute both values now, then verify. macOS does its own
        # substitution in installers/macos/install-macos.sh.
        _python_cmd="$(ods_detect_python_cmd 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
        _hermes_tpl="$INSTALL_DIR/extensions/services/hermes/cli-config.yaml.template"
        if [[ -f "$_hermes_tpl" ]]; then
            # Model name: cloud mode uses the routed model id; Lemonade
            # prefixes GGUF files with "extra."; llama.cpp uses the file name.
            if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
                _hermes_model="${LLM_MODEL:-default}"
            elif _phase11_external_lemonade; then
                _hermes_model="${LEMONADE_MODEL:-$(_phase11_env_get LEMONADE_MODEL "${LLM_MODEL:-default}")}"
            else
                _hermes_model="$GGUF_FILE"
            fi
            if [[ "${GPU_BACKEND:-}" == "amd" && "${ODS_MODE:-local}" != "cloud" ]] && ! _phase11_external_lemonade; then
                _hermes_model="extra.$GGUF_FILE"
            fi
            # base_url: on AMD/Lemonade hosts, route Hermes through litellm
            # instead of direct-to-Lemonade. Lemonade is strict about model
            # names and rejects concurrent connections that show up during a
            # multi-step agent loop (web_search → reason → tool result →
            # reason …), which results in APIConnectionError mid-tool-loop.
            # litellm's "*" wildcard model_list normalises the model name and
            # adds upstream retry logic. On non-AMD Linux installs there's a
            # sibling llama-server container that takes any model name; on
            # macOS install-macos.sh handles the host.docker.internal swap.
            _hermes_base_url=""
            _hermes_api_key=""
            if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
                _hermes_base_url="${HERMES_LLM_BASE_URL:-http://litellm:4000/v1}"
                _hermes_api_key="${HERMES_LLM_API_KEY:-${LITELLM_KEY:-}}"
            elif [[ "${GPU_BACKEND:-}" == "amd" ]] || _phase11_external_lemonade; then
                _hermes_base_url="http://litellm:4000/v1"
                _hermes_api_key="${LITELLM_KEY:-}"
            fi
            _hermes_context="${MAX_CONTEXT:-65536}"
            _hermes_request_timeout=180
            if [[ "${ODS_MODE:-local}" != "cloud" ]] && { [[ "${GPU_BACKEND:-}" == "amd" ]] || _phase11_external_lemonade; }; then
                _hermes_request_timeout=900
            fi
            _hermes_patcher="$INSTALL_DIR/scripts/patch-hermes-config.py"
            if [[ -n "$_python_cmd" && -f "$_hermes_patcher" ]]; then
                _hermes_patcher_args=("$_hermes_tpl" --model "$_hermes_model" --context-length "$_hermes_context")
                if [[ -n "$_hermes_base_url" ]]; then
                    _hermes_patcher_args+=(--base-url "$_hermes_base_url")
                fi
                if [[ -n "$_hermes_api_key" ]]; then
                    _hermes_patcher_args+=(--api-key "$_hermes_api_key")
                fi
                _hermes_patcher_args+=(--request-timeout-seconds "$_hermes_request_timeout")
                "$_python_cmd" "$_hermes_patcher" "${_hermes_patcher_args[@]}" >>"$LOG_FILE" 2>&1 || \
                    warn "Hermes config patcher failed for $_hermes_tpl"
            else
                sed -i.bak \
                    -e "s|^  default: \"qwen3.5-9b\"|  default: \"$_hermes_model\"|" \
                    -e "s|^  context_length: .*|  context_length: ${_hermes_context}|" \
                    -e "s|^    context_length: .*|    context_length: ${_hermes_context}|" \
                    -e "s|^    request_timeout_seconds: 180[[:space:]]*$|    request_timeout_seconds: ${_hermes_request_timeout}|" \
                    "$_hermes_tpl" 2>>"$LOG_FILE" && rm -f "${_hermes_tpl}.bak"
            fi
            if grep -q "^  default: \"$_hermes_model\"$" "$_hermes_tpl" && \
               grep -q "^  context_length: ${_hermes_context}$" "$_hermes_tpl"; then
                ai_ok "Patched Hermes template: model.default=$_hermes_model, context=$_hermes_context"
            else
                warn "Hermes template substitution didn't take effect — Hermes may 404 every chat completion. Hand-edit $_hermes_tpl after install if Hermes prompts hang."
            fi
        fi

        # Render data/persona/SOUL.md = static persona + dynamic installation
        # context (GPU backend, model, running services, reachable URLs). The
        # Hermes compose bind-mounts this file as /opt/hermes/docker/SOUL.md
        # so the agent introspects truthfully when asked about its own
        # environment instead of inventing capabilities.
        #
        # docker ps right here may return nothing useful (services aren't up
        # until later in this phase), but the script still emits a valid
        # SOUL.md with the static parts intact. `ods restart hermes`
        # regenerates it once services are running.
        _soul_builder="$INSTALL_DIR/scripts/build-installation-context.py"
        _soul_output="$INSTALL_DIR/data/persona/SOUL.md"
        _soul_template="$INSTALL_DIR/extensions/services/hermes/SOUL.md.template"
        mkdir -p "$(dirname "$_soul_output")"
        if [[ -e "$_soul_output" && ! -f "$_soul_output" ]]; then
            rm -rf "$_soul_output" || \
                warn "Could not replace invalid Hermes SOUL.md path at $_soul_output"
        fi
        if [[ -n "$_python_cmd" && -f "$_soul_builder" ]]; then
            "$_python_cmd" "$_soul_builder" >>"$LOG_FILE" 2>&1 || \
                warn "Could not generate Hermes installation-context SOUL.md (non-fatal — Hermes will use the template's default text)"
        fi
        if [[ ! -f "$_soul_output" && -f "$_soul_template" ]]; then
            sed '/<!-- INSTALLATION_CONTEXT -->/d' "$_soul_template" >"$_soul_output" || \
                warn "Could not create fallback Hermes SOUL.md at $_soul_output"
        fi
    fi

    # Validate service dependencies before launching
    if [[ -f "$INSTALL_DIR/lib/service-registry.sh" && -f "$INSTALL_DIR/lib/validate-dependencies.sh" ]]; then
        . "$INSTALL_DIR/lib/service-registry.sh"
        . "$INSTALL_DIR/lib/validate-dependencies.sh"
        sr_load

        ai "Validating service dependencies..."
        if ! validate_service_dependencies; then
            ai_bad "Service dependency validation failed"
            ai "Some services depend on other services that are not enabled"
            ai "Enable required services or disable dependent services to continue"
            exit 1
        fi
        ai_ok "All service dependencies satisfied"
    fi

    # ── Compose syntax validation ──────────────────────────────
    ai "Validating compose stack configuration..."
    if ! $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" config --quiet 1>/dev/null 2>"$LOG_FILE.compose-check"; then
        ai_bad "Compose configuration is invalid"
        ai "Check $LOG_FILE.compose-check for details"
        cat "$LOG_FILE.compose-check" >&2
        exit 1
    fi
    ai_ok "Compose configuration valid"

    # Launch containers
    ods_progress 81 "services" "Launching containers"
    echo ""
    signal "Waking the stack..."
    ai "I'm bringing systems online. You can breathe."
    echo ""
    COMPOSE_STARTED_WITH_DELAYED_HEALTH=false
    compose_ok=false
    # Build local images individually so every failure is reported before the
    # installer refuses to launch any potentially stale image.
    _candidate_build_services=(dashboard dashboard-api ape token-spy privacy-shield)
    [[ "$ENABLE_COMFYUI" == "true" ]] && _candidate_build_services+=(comfyui)
    [[ "$GPU_BACKEND" == "amd" ]] && _candidate_build_services+=(llama-server)
    if ! _enabled_compose_services="$($DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" config --services 2>>"$LOG_FILE")"; then
        ai_bad "Could not resolve compose services before local image builds."
        ai "Inspect compose config with: $(_phase11_compose_command_text) config --services"
        exit 1
    fi
    _build_services=()
    for _svc in "${_candidate_build_services[@]}"; do
        if printf '%s\n' "$_enabled_compose_services" | grep -qx "$_svc"; then
            _build_services+=("$_svc")
        else
            log "Skipping local image build for disabled service: $_svc"
        fi
    done
    if [[ "$GPU_BACKEND" == "nvidia" && " ${_build_services[*]} " == *" comfyui "* ]]; then
        ai "ComfyUI is compiling from source for NVIDIA — this takes 25-40 minutes on first run."
    fi
    if ! _phase11_build_local_images "${_build_services[@]}"; then
        exit 1
    fi

    # Start everything. --no-build is intentional: the explicit build loop
    # above successfully produced every enabled buildable image,
    # and we don't want compose-up silently re-invoking the slow ComfyUI build
    # on each retry. --pull never is intentional too: Phase 08 and the preflight
    # below own registry access through pull_with_progress, so compose-up cannot
    # die mid-launch on an unbounded TLS handshake timeout.
    # Up to 3 attempts with increasing wait between retries — on AMD/Lemonade,
    # the first boot builds a cached llama-server binary which can take 3-5 min.
    if ! _phase11_pre_pull_compose_images; then
        exit 1
    fi
    _phase11_write_compose_launch_record
    for _attempt in 1 2 3; do
        $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" up -d --remove-orphans --no-build --pull never >> "$LOG_FILE" 2>&1 &
        compose_pid=$!
        if spin_task $compose_pid "Launching containers (attempt $_attempt/3)..."; then
            compose_ok=true
            break
        fi
        if [[ $_attempt -lt 3 ]]; then
            printf "\r  ${AMB}⚠${NC} %-60s\n" "Some services still starting..."
            ai_warn "Some containers need more time. Waiting 30s before retry..."
            sleep 30
        fi
    done
    # Safety net: when --no-build hits a missing image, compose aborts before
    # starting other containers. Some end up in "Created", others never got
    # past "Creating" because their dependencies weren't ready yet.
    # Step 1: start any containers already in Created state
    $DOCKER_CMD start $($DOCKER_CMD ps -a --filter status=created -q) 2>/dev/null || true
    # Step 2: wait for services to stabilize, then compose pass
    sleep 10
    $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" up -d --remove-orphans --no-build --pull never >> "$LOG_FILE" 2>&1 || true
    # Step 3: catch any stragglers from the second pass
    $DOCKER_CMD start $($DOCKER_CMD ps -a --filter status=created -q) 2>/dev/null || true

    # If ODS_AGENT_BIND is unset, the Linux host-agent binds to the ODS
    # Docker network gateway once that network exists. Phase 07 may have
    # started it before compose created ods-network, so restart it here to
    # let the safer scoped bind take effect.
    if [[ -z "${ODS_AGENT_BIND:-}" ]] \
      && [[ "$(uname -s 2>/dev/null || echo unknown)" == "Linux" ]] \
      && command -v systemctl >/dev/null 2>&1 \
      && sudo -n systemctl is-enabled ods-host-agent.service >/dev/null 2>&1; then
        if sudo -n systemctl restart ods-host-agent.service 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
            ai_ok "Restarted ods-host-agent after ods-network creation"
        else
            ai_warn "ods-host-agent restart after network creation failed (non-fatal)"
        fi
    fi

    # dashboard-api reaches the host agent from the compose network gateway.
    # With default-DROP UFW/firewalld, the host INPUT chain can block that
    # traffic. Add a scoped rule only after compose has created ods-network,
    # so we allow the actual Docker subnet instead of a broad RFC1918 range.
    _phase11_allow_host_agent_firewall ods-network
    _phase11_allow_external_lemonade_firewall ods-network

    _compose_started_with_delayed_health=false
    if ! $compose_ok && _phase11_compose_failure_is_delayed_health && _phase11_has_managed_containers; then
        # docker compose treats `depends_on: condition: service_healthy` as a
        # hard failure when a dependency is still cold-loading at the end of its
        # healthcheck window. Large GGUFs can legitimately cross that window on
        # reinstall/upgrade, while the containers are already created and phase
        # 12 has the long adaptive health wait. Other compose failures still
        # take the fatal path below.
        _compose_started_with_delayed_health=true
        COMPOSE_STARTED_WITH_DELAYED_HEALTH=true
        compose_ok=true
    fi

    if $compose_ok; then
        if $_compose_started_with_delayed_health; then
            printf "\r  ${AMB}⚠${NC} %-60s\n" "Containers launched; waiting on health checks"
            echo ""
            ai_warn "Some containers are still becoming healthy. Continuing to the longer health checks."
        else
            if ! _phase11_assert_managed_containers; then
                exit 1
            fi
            printf "\r  ${BGRN}✓${NC} %-60s\n" "All containers launched"
            echo ""
            ai_ok "Services started (llama-server)"
        fi

        # Re-render data/persona/SOUL.md now that services are actually
        # running — the first pass earlier in this phase happened pre-
        # compose-up, so its docker ps was empty. The persona's "About
        # this installation" section needs to reflect what's actually
        # reachable, not the pre-launch state. Hermes reads from
        # /opt/data/SOUL.md at session time; because macOS Docker Desktop
        # rejects the old nested bind mount, copy the generated file into
        # the running container instead.
        if [[ -n "${_python_cmd:-}" ]] && [[ -f "$INSTALL_DIR/scripts/build-installation-context.py" ]]; then
            "$_python_cmd" "$INSTALL_DIR/scripts/build-installation-context.py" >>"$LOG_FILE" 2>&1 || \
                warn "Installation-context SOUL.md regen failed post-launch (non-fatal — earlier static SOUL.md is in place)"
            if $DOCKER_CMD ps --format '{{.Names}}' 2>/dev/null | grep -qx 'ods-hermes'; then
                $DOCKER_CMD exec ods-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md \
                    >>"$LOG_FILE" 2>&1 || \
                    warn "Could not sync installation-context SOUL.md into running Hermes container"
            fi
        fi
    else
        printf "\r  ${RED}✗${NC} %-60s\n" "Some containers failed to launch"
        echo ""
        ai_warn "Some services failed. Check: docker compose logs"
        ai_warn "Log file: $LOG_FILE"
        if command -v write_compose_failure_report >/dev/null 2>&1; then
            _compose_up_suffix="$(_phase11_compose_up_suffix)"
            _compose_report_path="$(write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-core phase 11 docker compose up" \
                "$DOCKER_COMPOSE_CMD $COMPOSE_FLAGS $_compose_up_suffix" \
                "$LOG_FILE" \
                "${GPU_BACKEND:-unknown}" \
                "Open the saved report, fix the failed image/port/compose error it identifies, then re-run ./install.sh." |
                tail -n 1)" || true
            [[ -n "${_compose_report_path:-}" ]] && ai_warn "Compose failure report saved: $_compose_report_path"
        fi
        if ! _phase11_assert_managed_containers; then
            exit 1
        fi
        exit 1
    fi

    # ── Bootstrap: launch background full-model download + auto hot-swap ──
    # Runs regardless of compose_ok — the download only needs disk + network.
    # bootstrap-upgrade.sh checks if Docker is running before attempting
    # hot-swap and handles it gracefully if containers aren't ready yet.
    if [[ "$_BOOTSTRAP_ACTIVE" == "true" ]]; then
        ai "Launching background download for $FULL_LLM_MODEL..."

        # Source background task tracking if not already loaded
        if ! command -v bg_task_start &>/dev/null && [[ -f "$SCRIPT_DIR/installers/lib/background-tasks.sh" ]]; then
            . "$SCRIPT_DIR/installers/lib/background-tasks.sh"
        fi

        _bootstrap_upgrade_args="$INSTALL_DIR/data/bootstrap-upgrade.args"
        {
            printf '%s\n' "$FULL_GGUF_FILE"
            printf '%s\n' "$FULL_GGUF_URL"
            printf '%s\n' "$FULL_GGUF_SHA256"
            printf '%s\n' "$FULL_LLM_MODEL"
            printf '%s\n' "$FULL_MAX_CONTEXT"
            printf '%s\n' "$BOOTSTRAP_GGUF_FILE"
        } > "$_bootstrap_upgrade_args.tmp" && mv "$_bootstrap_upgrade_args.tmp" "$_bootstrap_upgrade_args" || \
            warn "Could not persist bootstrap-upgrade retry metadata"
        chmod 600 "$_bootstrap_upgrade_args" 2>/dev/null || true

        # Start the long-lived downloader from a child shell that closes inherited
        # non-stdio FDs first. Otherwise caller-owned advisory locks (FD 9, FD
        # 200, etc.) can stay held until the model download exits.
        (
            _phase11_close_inherited_fds_for_daemon
            exec nohup bash "$SCRIPT_DIR/scripts/bootstrap-upgrade.sh" \
                "$INSTALL_DIR" "$FULL_GGUF_FILE" "$FULL_GGUF_URL" \
                "$FULL_GGUF_SHA256" "$FULL_LLM_MODEL" "$FULL_MAX_CONTEXT" \
                "$BOOTSTRAP_GGUF_FILE" \
                > "$INSTALL_DIR/logs/model-upgrade.log" 2>&1
        ) &
        _upgrade_pid=$!

        if command -v bg_task_start &>/dev/null; then
            bg_task_start "full-model-download" "$_upgrade_pid" \
                "Full model download: $FULL_LLM_MODEL" \
                "$INSTALL_DIR/logs/model-upgrade.log"
        fi

        log "Background model upgrade started (PID: $_upgrade_pid)"
        ai "Full model ($FULL_LLM_MODEL) downloading in background."
        ai "It will auto-swap when ready. Check progress: tail -f $INSTALL_DIR/logs/model-upgrade.log"
    fi

    ods_progress 83 "services" "Running extension setup hooks"
    # ── Run extension setup hooks ──
    if [[ -f "$INSTALL_DIR/lib/service-registry.sh" ]]; then
        _HOOK_DIR="$INSTALL_DIR"
        . "$_HOOK_DIR/lib/service-registry.sh"
        sr_load
        _hook_count=0
        for sid in "${SERVICE_IDS[@]}"; do
            hook="${SERVICE_SETUP_HOOKS[$sid]:-}"
            [[ -z "$hook" || ! -f "$hook" ]] && continue
            [[ -x "$hook" ]] || chmod +x "$hook"
            log "Running setup hook for $sid: $hook"
            if bash "$hook" "$INSTALL_DIR" "$GPU_BACKEND" >> "$LOG_FILE" 2>&1; then
                _hook_count=$((_hook_count + 1))
            else
                ai_warn "Setup hook for $sid exited with error (non-fatal)"
            fi
        done
        [[ $_hook_count -gt 0 ]] && ai_ok "Ran $_hook_count extension setup hook(s)" || true
    fi
fi
