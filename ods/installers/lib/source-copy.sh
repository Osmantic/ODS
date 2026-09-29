#!/bin/bash
# Keep the owner's provider configuration across source upgrades. Docker may
# still bind its old inode, so replacing it can hide a broken next startup.
ods_copy_install_source() {
    local source_dir="$1" install_dir="$2" log_file="$3"
    local cloud="$install_dir/config/litellm/cloud.yaml" parent metadata owner group mode
    local uid gid private_group=false
    local -a cloud_excludes=() tighten=()

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
        # Installs made before phase 06 normalized code modes (v2.6.0 and
        # earlier) under the user-private-group umask 002 carry group-write on
        # these paths, and this check runs before that normalization. Group
        # write for the owner's own private group (named after the user) grants
        # no one else access, so tighten it; any other group or world write
        # still refuses.
        uid="$(id -u)"; gid="$(id -g)"
        if [[ "$(id -gn)" == "$(id -un)" ]]; then
            private_group=true
        fi
        for parent in "$install_dir" "$install_dir/config" "$install_dir/config/litellm" "$cloud"; do
            metadata="$(stat -c '%u:%g:%a' -- "$parent")" || return 1
            IFS=: read -r owner group mode <<< "$metadata"
            if [[ "$owner" != "$uid" || ! "$mode" =~ ^[0-7]{3,4}$ ]] \
                || (( (8#$mode & 8#002) != 0 )) \
                || { (( (8#$mode & 8#020) != 0 )) && [[ "$private_group" != true || "$group" != "$gid" ]]; }; then
                error "Existing cloud provider configuration must have safe owner and write permissions: $parent"
                return 1
            fi
            if (( (8#$mode & 8#020) != 0 )); then
                tighten+=("$parent")
            fi
        done
        cloud_excludes=(--exclude='/config/litellm/cloud.yaml')
        command -v rsync >/dev/null 2>&1 || {
            error "Install rsync before upgrading an existing cloud provider configuration."
            return 1
        }
        if (( ${#tighten[@]} )); then
            chmod g-w -- "${tighten[@]}" || return 1
        fi
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
