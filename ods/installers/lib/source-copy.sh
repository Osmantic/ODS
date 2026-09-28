#!/bin/bash
# Keep the owner's provider configuration across source upgrades. Docker may
# still bind its old inode, so replacing it can hide a broken next startup.
ods_copy_install_source() {
    local source_dir="$1" install_dir="$2" log_file="$3"
    local cloud="$install_dir/config/litellm/cloud.yaml" parent metadata owner mode
    local -a cloud_excludes=()

    if [[ -e "$cloud" || -L "$cloud" ]]; then
        [[ -f "$cloud" && ! -L "$cloud" ]] || {
            error "Existing cloud provider configuration is not a regular file."
            return 1
        }
        for parent in "$install_dir" "$install_dir/config" "$install_dir/config/litellm"; do
            [[ -d "$parent" && ! -L "$parent" ]] || {
                error "Existing cloud provider configuration has an unsafe parent."
                return 1
            }
        done
        for parent in "$install_dir" "$install_dir/config" "$install_dir/config/litellm" "$cloud"; do
            metadata="$(stat -c '%u:%a' -- "$parent")" || return 1
            owner="${metadata%%:*}"; mode="${metadata#*:}"
            [[ "$owner" == "$(id -u)" && "$mode" =~ ^[0-7]{3,4}$ ]] \
                && (( (8#$mode & 8#022) == 0 )) || {
                    error "Existing cloud provider configuration must have safe owner and write permissions."
                    return 1
                }
        done
        cloud_excludes=(--exclude='/config/litellm/cloud.yaml')
        command -v rsync >/dev/null 2>&1 || {
            error "Install rsync before upgrading an existing cloud provider configuration."
            return 1
        }
    fi

    [[ "$source_dir" != "$install_dir" ]] || return 0
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --no-owner --no-group \
            --exclude='.git' --exclude='data/' --exclude='logs/' \
            --exclude='models/' --exclude='.env' --exclude='node_modules/' \
            --exclude='dist/' --exclude='*.log' --exclude='.current-mode' \
            --exclude='.profiles' --exclude='.target-model' \
            --exclude='.target-quantization' --exclude='.offline-mode' \
            "${cloud_excludes[@]}" "$source_dir/" "$install_dir/"
    else
        # This fallback is used only when no existing provider leaf needs
        # preservation. A rerun cannot safely use an unfiltered recursive cp.
        cp -r "$source_dir"/* "$install_dir/" 2>>"$log_file" || return 1
        cp "$source_dir/.gitignore" "$install_dir/" 2>>"$log_file" || return 1
    fi
}
