#!/usr/bin/env bash
# Remote-provider containers receive the installation data group, not the
# installation owner's UID. Their private state therefore needs group traversal.
ods_create_remote_provider_directories() {
    local install_dir="$1" directory
    for directory in "$install_dir/data" "$install_dir/data/remote-provider" \
        "$install_dir/data/remote-provider/secrets"; do
        if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
            printf 'Refusing unsafe remote-provider state directory: %s\n' "$directory" >&2
            return 1
        fi
    done
    mkdir -p "$install_dir/data/remote-provider/secrets" || return 1
}

ods_prepare_remote_provider_directories() {
    local install_dir="$1" directory
    ods_create_remote_provider_directories "$install_dir" || return 1
    for directory in "$install_dir/data/remote-provider" "$install_dir/data/remote-provider/secrets"; do
        chgrp "$(id -g)" "$directory" && chmod 750 "$directory" || return 1
    done
}
