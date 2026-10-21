#!/bin/bash
# Keep the owner's provider configuration across source upgrades. Docker may
# still bind its old inode, so replacing it can hide a broken next startup.
ods_copy_install_source() {
    local source_dir="$1" install_dir="$2" log_file="$3"
    local cloud="$install_dir/config/litellm/cloud.yaml" dotfile helper
    local -a cloud_excludes=() held_source_excludes=()
    if [[ -n "${ODS_PIXEL_SOURCE_TRANSACTION:-}" ]]; then
        # The protected source transaction has already installed and verified
        # these exact trees under admission hold. Do not overwrite them again
        # with a second unchecked copy or expose a mixed source tree on retry.
        held_source_excludes=(--exclude='/bin/' --exclude='/lib/' --exclude='/scripts/'
            --exclude='/installers/' --exclude='/extensions/' --exclude='/vendor/')
        command -v rsync >/dev/null 2>&1 || return 1
    fi

    if [[ -e "$cloud" || -L "$cloud" ]]; then
        command -v rsync >/dev/null 2>&1 || {
            error "Install rsync before upgrading an existing cloud provider configuration."
            return 1
        }
        # Validate every path before changing modes. Keep the helper tied to
        # this installer source, not the retained installation being checked.
        helper="${BASH_SOURCE[0]%/*}/cloud-config-permissions.py"
        "${ODS_PYTHON_CMD:-python3}" "$helper" "$install_dir" || return 1
        cloud_excludes=(--exclude='/config/litellm/cloud.yaml')
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
            "${cloud_excludes[@]}" "${held_source_excludes[@]}" "$source_dir/" "$install_dir/"
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
