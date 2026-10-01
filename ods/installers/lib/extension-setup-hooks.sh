#!/usr/bin/env bash
# Run post-install hooks only for extension fragments in the active Compose plan.

ods_run_selected_extension_setup_hooks() (
    local install_dir="$1" gpu_backend="$2" log_file="$3"
    shift 3
    local -a compose_flags=("$@")
    # The sourced registry uses SCRIPT_DIR to locate installed manifests.
    # shellcheck disable=SC2034
    local SCRIPT_DIR="$install_dir"
    local sid hook compose_file flag_file selected count=0
    local i

    [[ -f "$install_dir/lib/service-registry.sh" ]] || return 0
    . "$install_dir/lib/service-registry.sh"
    sr_load

    for sid in "${SERVICE_IDS[@]}"; do
        hook="${SERVICE_SETUP_HOOKS[$sid]:-}"
        compose_file="${SERVICE_COMPOSE[$sid]:-}"
        [[ -f "$hook" && -f "$compose_file" ]] || continue

        selected=false
        for ((i = 0; i + 1 < ${#compose_flags[@]}; i++)); do
            [[ "${compose_flags[i]}" == "-f" ]] || continue
            flag_file="${compose_flags[i + 1]}"
            if [[ "$flag_file" == "$compose_file" ||
                  "$install_dir/${flag_file#./}" == "$compose_file" ]]; then
                selected=true
                break
            fi
        done
        if ! $selected; then
            log "Skipping setup hook for unselected extension: $sid"
            continue
        fi

        log "Running setup hook for $sid: $hook"
        if bash "$hook" "$install_dir" "$gpu_backend" >> "$log_file" 2>&1; then
            count=$((count + 1))
        else
            ai_warn "Setup hook for $sid exited with error (non-fatal)"
        fi
    done
    if (( count > 0 )); then
        ai_ok "Ran $count extension setup hook(s)"
    fi
)
