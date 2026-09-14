#!/usr/bin/env bash

# copy_oauth_credentials INSTALL_DIR
# Copies pre-registered OAuth credentials into the Hermes home directory.
# Does not overwrite existing credentials, preserving operator overrides.
copy_oauth_credentials() {
    local install_dir="${1:-}"
    [[ -z "$install_dir" ]] && return 1

    local creds_dir="$install_dir/extensions/services/hermes/credentials"
    local hermes_data="$install_dir/data/hermes"

    # Define logging fallbacks if installer UI helpers aren't loaded
    local log_info="echo"
    local log_success="echo"
    local log_warn="echo"
    type ai >/dev/null 2>&1 && log_info="ai"
    type ai_ok >/dev/null 2>&1 && log_success="ai_ok"
    type ai_warn >/dev/null 2>&1 && log_warn="ai_warn"

    if [[ -d "$creds_dir" ]]; then
        local found_creds=false
        for cred in "$creds_dir/"*.json; do
            if [[ -f "$cred" ]]; then
                found_creds=true
                break
            fi
        done

        if $found_creds; then
            $log_info "Bundled OAuth credentials detected in $creds_dir"
            mkdir -p "$hermes_data" 2>/dev/null || true
            for cred in "$creds_dir/"*.json; do
                [[ -f "$cred" ]] || continue
                local fname
                fname="$(basename "$cred")"
                local target="$hermes_data/$fname"

                # Sudo-aware file existence check
                local exists=false
                if [[ -f "$target" ]] || sudo -n test -f "$target" 2>/dev/null; then
                    exists=true
                fi

                if ! $exists; then
                    cp -n "$cred" "$target" 2>/dev/null || sudo -n cp -n "$cred" "$target" 2>/dev/null || true

                    # Verify successful copy using sudo-aware logic
                    local verify_exists=false
                    if [[ -f "$target" ]] || sudo -n test -f "$target" 2>/dev/null; then
                        verify_exists=true
                    fi

                    if $verify_exists; then
                        sudo -n chown 10000:10000 "$target" 2>/dev/null || true
                        $log_success "Copied OAuth credential: $fname"
                    else
                        $log_warn "Failed to copy OAuth credential: $fname"
                    fi
                else
                    $log_info "Preserved existing OAuth credential: $fname (operator override)"
                fi
            done
        fi
    fi
}
