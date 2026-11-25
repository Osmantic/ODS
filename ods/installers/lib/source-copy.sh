#!/bin/bash
# Keep owner-managed configuration across source upgrades. Docker may
# still bind its old inode, so replacing it can hide a broken next startup.
ods_prepare_retained_cloud_provider() {
    local install_dir="$1" cloud helper
    cloud="$install_dir/config/litellm/cloud.yaml"
    [[ -e "$cloud" || -L "$cloud" ]] || return 0
    command -v rsync >/dev/null 2>&1 || {
        error "Install rsync before upgrading an existing cloud provider configuration."
        return 1
    }
    # Use this installer source, never a helper from the retained installation.
    helper="${BASH_SOURCE[0]%/*}/cloud-config-permissions.py"
    "${ODS_PYTHON_CMD:-python3}" "$helper" "$install_dir" || return 1
}

ods_validate_searxng_settings_path() {
    local install_dir="$1" config_dir searxng_dir settings
    config_dir="$install_dir/config"
    searxng_dir="$config_dir/searxng"
    settings="$searxng_dir/settings.yml"
    if [[ -L "$config_dir" ]]; then
        error "Refusing to use symlinked config directory: $config_dir"
        return 1
    fi
    if [[ -e "$config_dir" && ! -d "$config_dir" ]]; then
        error "Refusing to use non-directory config path: $config_dir"
        return 1
    fi
    if [[ -L "$searxng_dir" ]]; then
        error "Refusing to use symlinked SearXNG config directory: $searxng_dir"
        return 1
    fi
    if [[ -e "$searxng_dir" && ! -d "$searxng_dir" ]]; then
        error "Refusing to use non-directory SearXNG config path: $searxng_dir"
        return 1
    fi
    if [[ -L "$settings" ]]; then
        error "Refusing to use symlinked SearXNG settings file: $settings"
        return 1
    fi
    if [[ -e "$settings" && ! -f "$settings" ]]; then
        error "Refusing to use non-regular SearXNG settings file: $settings"
        return 1
    fi
    return 0
}

ods_copy_install_source() {
    local source_dir="$1" install_dir="$2" log_file="$3"
    local cloud="$install_dir/config/litellm/cloud.yaml" dotfile
    local -a cloud_excludes=() held_source_excludes=() searxng_excludes=()
    ods_validate_searxng_settings_path "$install_dir" || return 1
    if [[ -n "${ODS_PIXEL_SOURCE_TRANSACTION:-}" ]]; then
        # The protected source transaction has already installed and verified
        # these exact trees under admission hold. Do not overwrite them again
        # with a second unchecked copy or expose a mixed source tree on retry.
        held_source_excludes=(--exclude='/bin/' --exclude='/lib/' --exclude='/scripts/'
            --exclude='/installers/' --exclude='/extensions/' --exclude='/vendor/')
        command -v rsync >/dev/null 2>&1 || return 1
    fi

    if [[ -e "$cloud" || -L "$cloud" ]]; then
        # Revalidate at the copy boundary, including direct library callers.
        ods_prepare_retained_cloud_provider "$install_dir" || return 1
        cloud_excludes=(--exclude='/config/litellm/cloud.yaml')
    fi

    # Protect the owner's bytes before the source template can replace them.
    # Force regeneration is handled by phase 06 using the configured secret.
    if [[ "${FORCE:-false}" != true && -f "$install_dir/config/searxng/settings.yml" ]]; then
        command -v rsync >/dev/null 2>&1 || {
            error "Install rsync before upgrading an existing SearXNG settings file."
            return 1
        }
        searxng_excludes=(--exclude='/config/searxng/settings.yml')
    fi

    [[ "$source_dir" != "$install_dir" ]] || return 0
    if command -v rsync >/dev/null 2>&1; then
        # DrvFS checkouts can report 0777 even for the source root. Keep
        # copied code and its parents safe before deferred reconciliation
        # checks the held source transaction; later hardening is too late.
        rsync -a --no-owner --no-group \
            --chmod=go-w \
            --exclude='.git' --exclude='data/' --exclude='logs/' \
            --exclude='models/' --exclude='.env' --exclude='node_modules/' \
            --exclude='dist/' --exclude='*.log' --exclude='.current-mode' \
            --exclude='.profiles' --exclude='.target-model' \
            --exclude='.target-quantization' --exclude='.offline-mode' \
            "${cloud_excludes[@]}" "${searxng_excludes[@]}" "${held_source_excludes[@]}" "$source_dir/" "$install_dir/"
    else
        # This fallback is used only when no existing provider leaf needs
        # preservation. A rerun cannot safely use an unfiltered recursive cp.
        cp -r "$source_dir"/* "$install_dir/" 2>>"$log_file" || return 1
        cp "$source_dir/.gitignore" "$install_dir/" 2>>"$log_file" || return 1
        # The glob above skips top-level dotfiles that rsync copies. Root-context
        # image builds read .dockerignore; the host agent's .env save, `ods
        # config validate` and the dashboard's settings sections read the .env
        # schema and example from the install. Sources from before a file
        # existed copy as they did.
        for dotfile in .dockerignore .env.example .env.schema.json; do
            if [[ -f "$source_dir/$dotfile" ]]; then
                cp "$source_dir/$dotfile" "$install_dir/" 2>>"$log_file" || return 1
            fi
        done
    fi
}
